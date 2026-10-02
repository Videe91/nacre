import asyncio
import logging
import weakref
from collections import deque

from . import asyn as fsspec_asyn
from .asyn import sync_teardown
from .utils import _fast_slice

logger = logging.getLogger(__name__)


class RunningAverageTracker:
    """Tracks a running average of values over a sliding window.

    This is used to monitor read sizes and adaptively scale the
    prefetching strategy based on recent user behavior.
    """

    def __init__(self, maxlen=10):
        """Initializes the tracker with a specific window size.

        Args:
            maxlen (int): The maximum number of historical values to keep.
        """
        logger.debug("Initializing RunningAverageTracker with maxlen: %d", maxlen)
        self._history = deque(maxlen=maxlen)
        self._sum = 0

    def add(self, value: int):
        """Adds a new value to the sliding window and updates the rolling sum.

        Args:
            value (int): The integer value to add to the history.
        """
        if value <= 0:
            raise ValueError(
                "Internal error, RunningAverageTracker tried inserting negative value"
            )
        if len(self._history) == self._history.maxlen:
            self._sum -= self._history[0]

        self._history.append(value)
        self._sum += value
        logger.debug(
            "RunningAverageTracker added value: %d, new sum: %d", value, self._sum
        )

    @property
    def average(self) -> int:
        """Calculates and returns the current running average.

        Returns:
            int: The integer average of the current history.
        """
        count = len(self._history)
        if count == 0:
            return 1024 * 1024  # 1MB
        return self._sum // count

    @property
    def is_variable(self) -> bool:
        """Determines if the history contains distinct chunk sizes."""
        count = len(self._history)
        if count < 2:
            return False

        return len(set(self._history)) > 1

    @property
    def last_value(self) -> int:
        """Returns the most recent entry in the history."""
        if not self._history:
            raise RuntimeError("No entry found in history")

        return self._history[-1]

    def clear(self):
        """Clears the history and resets the sum to zero."""
        logger.debug("Clearing RunningAverageTracker history.")
        self._history.clear()
        self._sum = 0


class PrefetchProducer:
    """Background worker that fetches sequential blocks of data.

    This class handles the network requests. It spawns asynchronous tasks
    to fetch data ahead of the user's current reading position and
    places those task promises into a queue for the consumer.
    """

    # If the request is too small, and prefetch window is expanded till 5MB
    # we then make request in 5MB blocks.
    MIN_CHUNK_SIZE = 5 * 1024 * 1024

    # If user doesn't specify any max_prefetch_size, the prefetcher defaults
    # to maximum of 2 * io_size and 128MB
    MIN_PREFETCH_SIZE = 128 * 1024 * 1024

    # The prefetching starts on the third read.
    MIN_STREAKS_FOR_PREFETCHING = 3

    # Threshold for disabling proactive prefetching on large, variable reads.
    #
    # If the average read size exceeds this value and patterns are variable,
    # prefetching shifts from an I/O bottleneck to a memory(CPU) bottleneck. When a user
    # requests random massive sizes (e.g., jumping between 64MB and INF), the
    # producer still fetches chunks based on the rolling average. The consumer
    # then has to pick up multiple chunks and stitch them together to match the
    # exact requested size.
    #
    # For small average read sizes, this byte assembly is fast and the bottleneck
    # remains the network I/O. However, for massive reads (>= 64MB), the extra
    # step of copying and assembling huge byte strings in memory severely slows
    # down the operation.
    VARIABLE_IO_THRESHOLD = 64 * 1024 * 1024

    def __init__(
        self,
        fetcher,
        size: int,
        concurrency: int,
        queue: asyncio.Queue,
        wakeup_event: asyncio.Event,
        consumer: "PrefetchConsumer",
        tracker: RunningAverageTracker,
        orchestrator: "BackgroundPrefetcher",
        user_max_prefetch_size=None,
    ):
        """Initializes the background producer.

        Args:
            fetcher (Callable): A coroutine function to fetch bytes from a remote source.
            size (int): Total size of the file being fetched.
            concurrency (int): Maximum number of concurrent fetch tasks.
            queue (asyncio.Queue): The shared queue to push download tasks into.
            wakeup_event (asyncio.Event): Event used to wake the producer from an idle state.
            consumer (PrefetchConsumer): The consumer reading the prefetched chunks.
            tracker (RunningAverageTracker): Tracker for history of read sizes.
            orchestrator (BackgroundPrefetcher): The parent object managing the operation.
            user_max_prefetch_size (int, optional): A hard limit for prefetch size overrides.
        """
        logger.debug(
            "Initializing PrefetchProducer: size=%d, concurrency=%d, user_max_prefetch_size=%s",
            size,
            concurrency,
            user_max_prefetch_size,
        )
        self.fetcher = fetcher
        self.size = size
        self.concurrency = concurrency
        self.queue = queue
        self.wakeup_event = wakeup_event

        self.consumer = consumer
        self.tracker = tracker
        self.orchestrator = weakref.proxy(orchestrator)
        self._user_max_prefetch_size = user_max_prefetch_size

        self.current_offset = 0
        self.is_stopped = False
        self._active_tasks = set()
        self._producer_task = None

    @property
    def max_prefetch_size(self) -> int:
        """Calculates the maximum prefetch size based on user intent or io size.

        Returns:
            int: The maximum number of bytes to prefetch ahead.
        """
        if self._user_max_prefetch_size is not None:
            return min(
                self._user_max_prefetch_size,
                max(2 * self.tracker.average, self.MIN_PREFETCH_SIZE),
            )
        return max(2 * self.tracker.average, self.MIN_PREFETCH_SIZE)

    def start(self):
        """Starts the background producer loop.

        This clears any previous wakeup events and spawns the main loop task.
        """
        logger.debug("Starting PrefetchProducer loop.")
        self.is_stopped = False
        self.wakeup_event.clear()
        self._producer_task = asyncio.create_task(self._loop())

    async def stop(self):
        """Cancels all active fetch tasks and shuts down the producer loop.

        This method ensures the queue is flushed and waits for cancelled
        tasks to finish cleaning up.
        """
        logger.debug(
            "Stopping PrefetchProducer. Active fetch tasks: %d", len(self._active_tasks)
        )
        self.is_stopped = True
        self.wakeup_event.set()

        tasks_to_wait = []
        if self._producer_task and not self._producer_task.done():
            self._producer_task.cancel()
            tasks_to_wait.append(self._producer_task)

        tasks_to_wait.extend(
            task for task in list(self._active_tasks) if not task.done()
        )

        # We do not cancel the network task, instead we wait on them.
        # This is intentionally done to avoid MRD stream disruption.
        self._active_tasks.clear()

        # Clear out any leftover items in the queue
        cleared_items = 0
        while not self.queue.empty():
            try:
                item = self.queue.get_nowait()
                if (
                    isinstance(item, asyncio.Task)
                    and item.done()
                    and not item.cancelled()
                ):
                    item.exception()
                cleared_items += 1
            except asyncio.QueueEmpty:
                break

        if cleared_items > 0:
            logger.debug(
                "Cleared %d leftover items from the queue during stop.", cleared_items
            )

        if tasks_to_wait:
            logger.debug(
                "Waiting for %d cancelled tasks to finish their teardown.",
                len(tasks_to_wait),
            )
            await asyncio.gather(*tasks_to_wait, return_exceptions=True)

        self.wakeup_event.clear()

    async def restart(self, new_offset: int):
        """Stops current tasks and restarts the background loop at a new byte offset.

        Args:
            new_offset (int): The new byte position to start prefetching from.
        """
        logger.debug("Restarting PrefetchProducer at new offset: %d", new_offset)
        await self.stop()
        self.current_offset = new_offset
        self.start()

    async def _loop(self):
        """The main background loop that delegates calculations and spawns tasks."""
        logger.debug("PrefetchProducer internal loop is now running.")
        try:
            while not self.is_stopped:
                await self.wakeup_event.wait()
                self.wakeup_event.clear()

                if self.is_stopped:
                    break

                await self._process_prefetch_cycle()

        except asyncio.CancelledError:
            logger.debug("PrefetchProducer loop was cancelled.")
        except Exception as e:
            logger.exception("PrefetchProducer loop encountered an unexpected error.")
            self.is_stopped = True
            self.orchestrator.set_error(e)
            await self.queue.put(e)

    def _calculate_prefetch_params(self) -> tuple[int, int, int]:
        """
        Evaluates current trackers and state to determine sizes.

        Returns:
            tuple: (prefetch_size, io_size, effective_prefetch_size)
        """
        avg_io_size = self.tracker.average
        streak = self.consumer.sequential_streak
        is_variable = self.tracker.is_variable
        last_read_size = self.tracker.last_value

        exceeds_user_max = (
            self._user_max_prefetch_size is not None
            and avg_io_size > self._user_max_prefetch_size
        )

        # Disable prefetching ahead if variable AND average > 64MB, or if it exceeds user max
        if (
            is_variable and avg_io_size > self.VARIABLE_IO_THRESHOLD
        ) or exceeds_user_max:
            logger.debug(
                "Large IO detected (variable > 64MB or > user max). Disabling background prefetching."
            )
            prefetch_multiplier = 1
        elif streak < self.MIN_STREAKS_FOR_PREFETCHING:
            prefetch_multiplier = 1
        else:
            prefetch_multiplier = streak - self.MIN_STREAKS_FOR_PREFETCHING + 1

        if self.queue.empty() or prefetch_multiplier == 1:
            io_size = last_read_size
        else:
            io_size = avg_io_size

        prefetch_size = min(prefetch_multiplier * io_size, self.max_prefetch_size)
        if self.consumer.offset + prefetch_size < self.consumer.target_offset:
            prefetch_size = self.consumer.target_offset - self.consumer.offset

        if is_variable:
            effective_prefetch_size = prefetch_size
        else:
            effective_prefetch_size = (prefetch_size // io_size) * io_size
            if effective_prefetch_size == 0:
                effective_prefetch_size = prefetch_size

        return prefetch_size, io_size, effective_prefetch_size

    async def _process_prefetch_cycle(self):
        """Executes a single cycle of enqueuing fetch tasks."""
        prefetch_size, io_size, effective_prefetch_size = (
            self._calculate_prefetch_params()
        )

        logger.debug(
            "Producer awake. Current offset: %d, User offset: %d, Prefetch size: %d",
            self.current_offset,
            self.consumer.offset,
            prefetch_size,
        )

        while (
            not self.is_stopped
            and (self.current_offset - self.consumer.offset) < prefetch_size
            and self.current_offset < self.size
        ):
            user_offset = self.consumer.offset
            space_remaining = self.size - self.current_offset
            prefetch_space_available = prefetch_size - (
                self.current_offset - user_offset
            )

            if prefetch_size >= self.MIN_CHUNK_SIZE:
                if prefetch_space_available >= self.MIN_CHUNK_SIZE:
                    actual_size = min(
                        max(self.MIN_CHUNK_SIZE, io_size), space_remaining
                    )
                else:
                    break
            else:
                actual_size = min(io_size, space_remaining)

            if prefetch_space_available < actual_size:
                if (
                    self.tracker.is_variable
                    or prefetch_space_available == prefetch_size
                ):
                    actual_size = prefetch_space_available
                else:
                    break

            streak = self.consumer.sequential_streak
            if streak < self.MIN_STREAKS_FOR_PREFETCHING:
                sfactor = self.concurrency
            else:
                sfactor = min(
                    self.concurrency,
                    max(
                        1,
                        actual_size * self.concurrency // effective_prefetch_size,
                    ),
                )

            logger.debug(
                "Spawning fetch task. Offset: %d, Size: %d, Split Factor: %d",
                self.current_offset,
                actual_size,
                sfactor,
            )

            download_task = asyncio.create_task(
                self.fetcher(self.current_offset, actual_size, split_factor=sfactor)
            )
            self._active_tasks.add(download_task)
            download_task.add_done_callback(self._active_tasks.discard)

            await self.queue.put(download_task)
            self.current_offset += actual_size

        if self.current_offset >= self.size:
            logger.debug("Producer reached EOF. Exiting background loop.")
            self.is_stopped = True


class PrefetchConsumer:
    """Consumes prefetched chunks from the queue and manages byte slicing.

    This class pulls data out of the shared queue and slices it into the
    exact byte sizes requested by the user. It also manages the local block buffer.
    """

    def __init__(
        self,
        queue: asyncio.Queue,
        wakeup_event: asyncio.Event,
        tracker: RunningAverageTracker,
        orchestrator: "BackgroundPrefetcher",
    ):
        """Initializes the consumer.

        Args:
            queue (asyncio.Queue): The shared queue containing fetch tasks.
            wakeup_event (asyncio.Event): Event used to wake the producer when more data is needed.
            tracker (RunningAverageTracker): Tracker for history of read sizes.
            orchestrator (BackgroundPrefetcher): The parent object managing the operation.
        """
        logger.debug("Initializing PrefetchConsumer.")
        self.queue = queue
        self.wakeup_event = wakeup_event
        self.tracker = tracker
        self.orchestrator = weakref.proxy(orchestrator)
        self.sequential_streak = 0
        self.offset = 0
        self.target_offset = 0
        self._current_block = b""
        self._current_block_idx = 0

    def seek(self, new_offset: int):
        """Clears the buffer and resets the internal offset for a hard seek.

        Args:
            new_offset (int): The byte position the consumer is jumping to.
        """
        logger.debug(
            "Consumer executing hard seek to offset %d. Clearing internal buffer.",
            new_offset,
        )
        self.offset = new_offset
        self.target_offset = new_offset
        self.sequential_streak = 0
        self._current_block = b""
        self._current_block_idx = 0

    def clear_buffer(self):
        """Discards the local byte buffer. Useful during shutdown or resets."""
        logger.debug("Consumer local block buffer cleared.")
        self._current_block = b""
        self._current_block_idx = 0

    async def _advance(self, size: int, save_data: bool) -> list[bytes]:
