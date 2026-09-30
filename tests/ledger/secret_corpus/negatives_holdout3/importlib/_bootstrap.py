"""Core implementation of import.

This module is NOT meant to be directly imported! It has been designed such
that it can be bootstrapped into Python as the implementation of import. As
such it requires the injection of specific modules and attributes in order to
work. One should use importlib as the public-facing version of this module.

"""
#
# IMPORTANT: Whenever making changes to this module, be sure to run a top-level
# `make regen-importlib` followed by `make` in order to get the frozen version
# of the module updated. Not doing so will result in the Makefile to fail for
# all others who don't have a ./python around to freeze the module
# in the early stages of compilation.
#

# See importlib._setup() for what is injected into the global namespace.

# When editing this code be aware that code executed at import time CANNOT
# reference any injected objects! This includes not only global code but also
# anything specified at the class level.

def _object_name(obj):
    try:
        return obj.__qualname__
    except AttributeError:
        return type(obj).__qualname__

# Bootstrap-related code ######################################################

# Modules injected manually by _setup()
_thread = None
_warnings = None
_weakref = None

# Import done by _install_external_importers()
_bootstrap_external = None


def _wrap(new, old):
    """Simple substitute for functools.update_wrapper."""
    for replace in ['__module__', '__name__', '__qualname__', '__doc__']:
        if hasattr(old, replace):
            setattr(new, replace, getattr(old, replace))
    new.__dict__.update(old.__dict__)


def _new_module(name):
    return type(sys)(name)


# Module-level locking ########################################################

# For a list that can have a weakref to it.
class _List(list):
    __slots__ = ("__weakref__",)


# Copied from weakref.py with some simplifications and modifications unique to
# bootstrapping importlib. Many methods were simply deleting for simplicity, so if they
# are needed in the future they may work if simply copied back in.
class _WeakValueDictionary:

    def __init__(self):
        self_weakref = _weakref.ref(self)

        # Inlined to avoid issues with inheriting from _weakref.ref before _weakref is
        # set by _setup(). Since there's only one instance of this class, this is
        # not expensive.
        class KeyedRef(_weakref.ref):

            __slots__ = "key",

            def __new__(type, ob, key):
                self = super().__new__(type, ob, type.remove)
                self.key = key
                return self

            def __init__(self, ob, key):
                super().__init__(ob, self.remove)

            @staticmethod
            def remove(wr):
                nonlocal self_weakref

                self = self_weakref()
                if self is not None:
                    if self._iterating:
                        self._pending_removals.append(wr.key)
                    else:
                        _weakref._remove_dead_weakref(self.data, wr.key)

        self._KeyedRef = KeyedRef
        self.clear()

    def clear(self):
        self._pending_removals = []
        self._iterating = set()
        self.data = {}

    def _commit_removals(self):
        pop = self._pending_removals.pop
        d = self.data
        while True:
            try:
                key = pop()
            except IndexError:
                return
            _weakref._remove_dead_weakref(d, key)

    def get(self, key, default=None):
        if self._pending_removals:
            self._commit_removals()
        try:
            wr = self.data[key]
        except KeyError:
            return default
        else:
            if (o := wr()) is None:
                return default
            else:
                return o

    def setdefault(self, key, default=None):
        try:
            o = self.data[key]()
        except KeyError:
            o = None
        if o is None:
            if self._pending_removals:
                self._commit_removals()
            self.data[key] = self._KeyedRef(default, key)
            return default
        else:
            return o


# A dict mapping module names to weakrefs of _ModuleLock instances.
# Dictionary protected by the global import lock.
_module_locks = {}

# A dict mapping thread IDs to weakref'ed lists of _ModuleLock instances.
# This maps a thread to the module locks it is blocking on acquiring.  The
# values are lists because a single thread could perform a re-entrant import
# and be "in the process" of blocking on locks for more than one module.  A
# thread can be "in the process" because a thread cannot actually block on
# acquiring more than one lock but it can have set up bookkeeping that reflects
# that it intends to block on acquiring more than one lock.
#
# The dictionary uses a WeakValueDictionary to avoid keeping unnecessary
# lists around, regardless of GC runs. This way there's no memory leak if
# the list is no longer needed (GH-106176).
_blocking_on = None


class _BlockingOnManager:
    """A context manager responsible to updating ``_blocking_on``."""
    def __init__(self, thread_id, lock):
        self.thread_id = thread_id
        self.lock = lock

    def __enter__(self):
        """Mark the running thread as waiting for self.lock. via _blocking_on."""
        # Interactions with _blocking_on are *not* protected by the global
        # import lock here because each thread only touches the state that it
        # owns (state keyed on its thread id).  The global import lock is
        # re-entrant (i.e., a single thread may take it more than once) so it
        # wouldn't help us be correct in the face of re-entrancy either.

        self.blocked_on = _blocking_on.setdefault(self.thread_id, _List())
        self.blocked_on.append(self.lock)

    def __exit__(self, *args, **kwargs):
        """Remove self.lock from this thread's _blocking_on list."""
        self.blocked_on.remove(self.lock)


class _DeadlockError(RuntimeError):
    pass



def _has_deadlocked(target_id, *, seen_ids, candidate_ids, blocking_on):
    """Check if 'target_id' is holding the same lock as another thread(s).

    The search within 'blocking_on' starts with the threads listed in
    'candidate_ids'.  'seen_ids' contains any threads that are considered
    already traversed in the search.

    Keyword arguments:
    target_id     -- The thread id to try to reach.
    seen_ids      -- A set of threads that have already been visited.
    candidate_ids -- The thread ids from which to begin.
    blocking_on   -- A dict representing the thread/blocking-on graph.  This may
                     be the same object as the global '_blocking_on' but it is
                     a parameter to reduce the impact that global mutable
                     state has on the result of this function.
    """
    if target_id in candidate_ids:
        # If we have already reached the target_id, we're done - signal that it
        # is reachable.
        return True

    # Otherwise, try to reach the target_id from each of the given candidate_ids.
    for tid in candidate_ids:
        if not (candidate_blocking_on := blocking_on.get(tid)):
            # There are no edges out from this node, skip it.
            continue
        elif tid in seen_ids:
            # bpo 38091: the chain of tid's we encounter here eventually leads
            # to a fixed point or a cycle, but does not reach target_id.
            # This means we would not actually deadlock.  This can happen if
            # other threads are at the beginning of acquire() below.
            return False
        seen_ids.add(tid)

        # Follow the edges out from this thread.
        edges = [lock.owner for lock in candidate_blocking_on]
        if _has_deadlocked(target_id, seen_ids=seen_ids, candidate_ids=edges,
                blocking_on=blocking_on):
            return True

    return False


class _ModuleLock:
    """A recursive lock implementation which is able to detect deadlocks
    (e.g. thread 1 trying to take locks A then B, and thread 2 trying to
    take locks B then A).
    """

    def __init__(self, name):
        # Create an RLock for protecting the import process for the
        # corresponding module.  Since it is an RLock, a single thread will be
        # able to take it more than once.  This is necessary to support
        # re-entrancy in the import system that arises from (at least) signal
        # handlers and the garbage collector.  Consider the case of:
        #
        #  import foo
        #  -> ...
        #     -> importlib._bootstrap._ModuleLock.acquire
        #        -> ...
        #           -> <garbage collector>
        #              -> __del__
        #                 -> import foo
        #                    -> ...
        #                       -> importlib._bootstrap._ModuleLock.acquire
        #                          -> _BlockingOnManager.__enter__
        #
        # If a different thread than the running one holds the lock then the
        # thread will have to block on taking the lock, which is what we want
        # for thread safety.
        self.lock = _thread.RLock()
        self.wakeup = _thread.allocate_lock()

        # The name of the module for which this is a lock.
        self.name = name

        # Can end up being set to None if this lock is not owned by any thread
        # or the thread identifier for the owning thread.
        self.owner = None

        # Represent the number of times the owning thread has acquired this lock
        # via a list of True.  This supports RLock-like ("re-entrant lock")
        # behavior, necessary in case a single thread is following a circular
        # import dependency and needs to take the lock for a single module
        # more than once.
        #
        # Counts are represented as a list of True because list.append(True)
        # and list.pop() are both atomic and thread-safe in CPython and it's hard
        # to find another primitive with the same properties.
        self.count = []

        # This is a count of the number of threads that are blocking on
        # self.wakeup.acquire() awaiting to get their turn holding this module
        # lock.  When the module lock is released, if this is greater than
        # zero, it is decremented and `self.wakeup` is released one time.  The
        # intent is that this will let one other thread make more progress on
        # acquiring this module lock.  This repeats until all the threads have
        # gotten a turn.
        #
        # This is incremented in self.acquire() when a thread notices it is
        # going to have to wait for another thread to finish.
        #
        # See the comment above count for explanation of the representation.
        self.waiters = []

    def has_deadlock(self):
        # To avoid deadlocks for concurrent or re-entrant circular imports,
        # look at _blocking_on to see if any threads are blocking
        # on getting the import lock for any module for which the import lock
        # is held by this thread.
        return _has_deadlocked(
            # Try to find this thread.
            target_id=_thread.get_ident(),
            seen_ids=set(),
            # Start from the thread that holds the import lock for this
            # module.
            candidate_ids=[self.owner],
            # Use the global "blocking on" state.
            blocking_on=_blocking_on,
        )

    def acquire(self):
        """
        Acquire the module lock.  If a potential deadlock is detected,
        a _DeadlockError is raised.
        Otherwise, the lock is always acquired and True is returned.
        """
        tid = _thread.get_ident()
        with _BlockingOnManager(tid, self):
            while True:
                # Protect interaction with state on self with a per-module
                # lock.  This makes it safe for more than one thread to try to
                # acquire the lock for a single module at the same time.
                with self.lock:
                    if self.count == [] or self.owner == tid:
                        # If the lock for this module is unowned then we can
                        # take the lock immediately and succeed.  If the lock
                        # for this module is owned by the running thread then
                        # we can also allow the acquire to succeed.  This
                        # supports circular imports (thread T imports module A
                        # which imports module B which imports module A).
                        self.owner = tid
                        self.count.append(True)
                        return True

                    # At this point we know the lock is held (because count !=
                    # 0) by another thread (because owner != tid).  We'll have
                    # to get in line to take the module lock.

                    # But first, check to see if this thread would create a
                    # deadlock by acquiring this module lock.  If it would
                    # then just stop with an error.
                    #
                    # It's not clear who is expected to handle this error.
                    # There is one handler in _lock_unlock_module but many
                    # times this method is called when entering the context
                    # manager _ModuleLockManager instead - so _DeadlockError
                    # will just propagate up to application code.
                    #
                    # This seems to be more than just a hypothetical -
                    # https://stackoverflow.com/questions/59509154
                    # https://github.com/encode/django-rest-framework/issues/7078
                    if self.has_deadlock():
                        raise _DeadlockError(f'deadlock detected by {self!r}')

                    # Check to see if we're going to be able to acquire the
                    # lock.  If we are going to have to wait then increment
                    # the waiters so `self.release` will know to unblock us
                    # later on.  We do this part non-blockingly so we don't
                    # get stuck here before we increment waiters.  We have
                    # this extra acquire call (in addition to the one below,
                    # outside the self.lock context manager) to make sure
                    # self.wakeup is held when the next acquire is called (so
                    # we block).  This is probably needlessly complex and we
                    # should just take self.wakeup in the return codepath
                    # above.
                    if self.wakeup.acquire(False):
                        self.waiters.append(None)

                # Now take the lock in a blocking fashion.  This won't
                # complete until the thread holding this lock
                # (self.owner) calls self.release.
                self.wakeup.acquire()

                # Taking the lock has served its purpose (making us wait), so we can
                # give it up now.  We'll take it w/o blocking again on the
                # next iteration around this 'while' loop.
                self.wakeup.release()

    def release(self):
        tid = _thread.get_ident()
        with self.lock:
            if self.owner != tid:
                raise RuntimeError('cannot release un-acquired lock')
            assert len(self.count) > 0
            self.count.pop()
            if not len(self.count):
                self.owner = None
                if len(self.waiters) > 0:
                    self.waiters.pop()
                    self.wakeup.release()

    def locked(self):
        return bool(self.count)

    def __repr__(self):
        return f'_ModuleLock({self.name!r}) at {id(self)}'


class _DummyModuleLock:
    """A simple _ModuleLock equivalent for Python builds without
    multi-threading support."""

    def __init__(self, name):
        self.name = name
        self.count = 0

    def acquire(self):
        self.count += 1
        return True

    def release(self):
        if self.count == 0:
            raise RuntimeError('cannot release un-acquired lock')
        self.count -= 1

    def __repr__(self):
