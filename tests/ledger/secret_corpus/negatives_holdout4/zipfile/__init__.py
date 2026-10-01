"""
Read and write ZIP files.

XXX references to utf-8 need further investigation.
"""
import binascii
import importlib.util
import io
import os
import shutil
import stat
import struct
import sys
import threading
import time

try:
    import zlib # We may need its compression method
    crc32 = zlib.crc32
except ImportError:
    zlib = None
    crc32 = binascii.crc32

try:
    import bz2 # We may need its compression method
except ImportError:
    bz2 = None

try:
    import lzma # We may need its compression method
except ImportError:
    lzma = None

try:
    from compression import zstd # We may need its compression method
except ImportError:
    zstd = None

__all__ = ["BadZipFile", "BadZipfile", "error",
           "ZIP_STORED", "ZIP_DEFLATED", "ZIP_BZIP2", "ZIP_LZMA",
           "ZIP_ZSTANDARD", "is_zipfile", "ZipInfo", "ZipFile", "PyZipFile",
           "LargeZipFile", "Path"]

class BadZipFile(Exception):
    pass


class LargeZipFile(Exception):
    """
    Raised when writing a zipfile, the zipfile requires ZIP64 extensions
    and those extensions are disabled.
    """

error = BadZipfile = BadZipFile      # Pre-3.2 compatibility names


ZIP64_LIMIT = (1 << 31) - 1
ZIP_FILECOUNT_LIMIT = (1 << 16) - 1
ZIP_MAX_COMMENT = (1 << 16) - 1

# constants for Zip file compression methods
ZIP_STORED = 0
ZIP_DEFLATED = 8
ZIP_BZIP2 = 12
ZIP_LZMA = 14
ZIP_ZSTANDARD = 93
# Other ZIP compression methods not supported

DEFAULT_VERSION = 20
ZIP64_VERSION = 45
BZIP2_VERSION = 46
LZMA_VERSION = 63
ZSTANDARD_VERSION = 63
# we recognize (but not necessarily support) all features up to that version
MAX_EXTRACT_VERSION = 63

# Below are some formats and associated data for reading/writing headers using
# the struct module.  The names and structures of headers/records are those used
# in the PKWARE description of the ZIP file format:
#     http://www.pkware.com/documents/casestudies/APPNOTE.TXT
# (URL valid as of January 2008)

# The "end of central directory" structure, magic number, size, and indices
# (section V.I in the format document)
structEndArchive = b"<4s4H2LH"
stringEndArchive = b"PK\005\006"
sizeEndCentDir = struct.calcsize(structEndArchive)

_ECD_SIGNATURE = 0
_ECD_DISK_NUMBER = 1
_ECD_DISK_START = 2
_ECD_ENTRIES_THIS_DISK = 3
_ECD_ENTRIES_TOTAL = 4
_ECD_SIZE = 5
_ECD_OFFSET = 6
_ECD_COMMENT_SIZE = 7
# These last two indices are not part of the structure as defined in the
# spec, but they are used internally by this module as a convenience
_ECD_COMMENT = 8
_ECD_LOCATION = 9

# The "central directory" structure, magic number, size, and indices
# of entries in the structure (section V.F in the format document)
structCentralDir = "<4s4B4HL2L5H2L"
stringCentralDir = b"PK\001\002"
sizeCentralDir = struct.calcsize(structCentralDir)

# indexes of entries in the central directory structure
_CD_SIGNATURE = 0
_CD_CREATE_VERSION = 1
_CD_CREATE_SYSTEM = 2
_CD_EXTRACT_VERSION = 3
_CD_EXTRACT_SYSTEM = 4
_CD_FLAG_BITS = 5
_CD_COMPRESS_TYPE = 6
_CD_TIME = 7
_CD_DATE = 8
_CD_CRC = 9
_CD_COMPRESSED_SIZE = 10
_CD_UNCOMPRESSED_SIZE = 11
_CD_FILENAME_LENGTH = 12
_CD_EXTRA_FIELD_LENGTH = 13
_CD_COMMENT_LENGTH = 14
_CD_DISK_NUMBER_START = 15
_CD_INTERNAL_FILE_ATTRIBUTES = 16
_CD_EXTERNAL_FILE_ATTRIBUTES = 17
_CD_LOCAL_HEADER_OFFSET = 18

# General purpose bit flags
# Zip Appnote: 4.4.4 general purpose bit flag: (2 bytes)
_MASK_ENCRYPTED = 1 << 0
# Bits 1 and 2 have different meanings depending on the compression used.
_MASK_COMPRESS_OPTION_1 = 1 << 1
# _MASK_COMPRESS_OPTION_2 = 1 << 2
# _MASK_USE_DATA_DESCRIPTOR: If set, crc-32, compressed size and uncompressed
# size are zero in the local header and the real values are written in the data
# descriptor immediately following the compressed data.
_MASK_USE_DATA_DESCRIPTOR = 1 << 3
# Bit 4: Reserved for use with compression method 8, for enhanced deflating.
# _MASK_RESERVED_BIT_4 = 1 << 4
_MASK_COMPRESSED_PATCH = 1 << 5
_MASK_STRONG_ENCRYPTION = 1 << 6
# _MASK_UNUSED_BIT_7 = 1 << 7
# _MASK_UNUSED_BIT_8 = 1 << 8
# _MASK_UNUSED_BIT_9 = 1 << 9
# _MASK_UNUSED_BIT_10 = 1 << 10
_MASK_UTF_FILENAME = 1 << 11
# Bit 12: Reserved by PKWARE for enhanced compression.
# _MASK_RESERVED_BIT_12 = 1 << 12
# _MASK_ENCRYPTED_CENTRAL_DIR = 1 << 13
# Bit 14, 15: Reserved by PKWARE
# _MASK_RESERVED_BIT_14 = 1 << 14
# _MASK_RESERVED_BIT_15 = 1 << 15

# The "local file header" structure, magic number, size, and indices
# (section V.A in the format document)
structFileHeader = "<4s2B4HL2L2H"
stringFileHeader = b"PK\003\004"
sizeFileHeader = struct.calcsize(structFileHeader)

_FH_SIGNATURE = 0
_FH_EXTRACT_VERSION = 1
_FH_EXTRACT_SYSTEM = 2
_FH_GENERAL_PURPOSE_FLAG_BITS = 3
_FH_COMPRESSION_METHOD = 4
_FH_LAST_MOD_TIME = 5
_FH_LAST_MOD_DATE = 6
_FH_CRC = 7
_FH_COMPRESSED_SIZE = 8
_FH_UNCOMPRESSED_SIZE = 9
_FH_FILENAME_LENGTH = 10
_FH_EXTRA_FIELD_LENGTH = 11

# The "Zip64 end of central directory locator" structure, magic number, and size
structEndArchive64Locator = "<4sLQL"
stringEndArchive64Locator = b"PK\x06\x07"
sizeEndCentDir64Locator = struct.calcsize(structEndArchive64Locator)

# The "Zip64 end of central directory" record, magic number, size, and indices
# (section V.G in the format document)
structEndArchive64 = "<4sQ2H2L4Q"
stringEndArchive64 = b"PK\x06\x06"
sizeEndCentDir64 = struct.calcsize(structEndArchive64)

_CD64_SIGNATURE = 0
_CD64_DIRECTORY_RECSIZE = 1
_CD64_CREATE_VERSION = 2
_CD64_EXTRACT_VERSION = 3
_CD64_DISK_NUMBER = 4
_CD64_DISK_NUMBER_START = 5
_CD64_NUMBER_ENTRIES_THIS_DISK = 6
_CD64_NUMBER_ENTRIES_TOTAL = 7
_CD64_DIRECTORY_SIZE = 8
_CD64_OFFSET_START_CENTDIR = 9

_DD_SIGNATURE = 0x08074b50


class _Extra(bytes):
    FIELD_STRUCT = struct.Struct('<HH')

    def __new__(cls, val, id=None):
        return super().__new__(cls, val)

    def __init__(self, val, id=None):
        self.id = id

    @classmethod
    def read_one(cls, raw):
        try:
            xid, xlen = cls.FIELD_STRUCT.unpack(raw[:4])
        except struct.error:
            xid = None
            xlen = 0
        return cls(raw[:4+xlen], xid), raw[4+xlen:]

    @classmethod
    def split(cls, data):
        # use memoryview for zero-copy slices
        rest = memoryview(data)
        while rest:
            extra, rest = _Extra.read_one(rest)
            yield extra

    @classmethod
    def strip(cls, data, xids):
        """Remove Extra fields with specified IDs."""
        return b''.join(
            ex
            for ex in cls.split(data)
            if ex.id not in xids
        )


def _check_zipfile(fp):
    try:
        endrec = _EndRecData(fp)
        if endrec:
            if endrec[_ECD_ENTRIES_TOTAL] == 0 and endrec[_ECD_SIZE] == 0 and endrec[_ECD_OFFSET] == 0:
                return True     # Empty zipfiles are still zipfiles
            elif endrec[_ECD_DISK_NUMBER] == endrec[_ECD_DISK_START]:
                # Central directory is on the same disk
                fp.seek(sum(_handle_prepended_data(endrec)))
                if endrec[_ECD_SIZE] >= sizeCentralDir:
                    data = fp.read(sizeCentralDir)   # CD is where we expect it to be
                    if len(data) == sizeCentralDir:
                        centdir = struct.unpack(structCentralDir, data) # CD is the right size
                        if centdir[_CD_SIGNATURE] == stringCentralDir:
                            return True         # First central directory entry  has correct magic number
    except OSError:
        pass
    return False

def is_zipfile(filename):
    """Quickly see if a file is a ZIP file by checking the magic number.

    The filename argument may be a file or file-like object too.
    """
    result = False
    try:
        if hasattr(filename, "read"):
            pos = filename.tell()
            result = _check_zipfile(fp=filename)
            filename.seek(pos)
        else:
            with open(filename, "rb") as fp:
                result = _check_zipfile(fp)
    except (OSError, BadZipFile):
        pass
    return result

def _handle_prepended_data(endrec, debug=0):
    size_cd = endrec[_ECD_SIZE]             # bytes in central directory
    offset_cd = endrec[_ECD_OFFSET]         # offset of central directory

    # "concat" is zero, unless zip was concatenated to another file
    concat = endrec[_ECD_LOCATION] - size_cd - offset_cd

    if debug > 2:
        inferred = concat + offset_cd
        print("given, inferred, offset", offset_cd, inferred, concat)

    return offset_cd, concat

def _EndRecData64(fpin, offset, endrec):
    """
    Read the ZIP64 end-of-archive records and use that to update endrec
    """
    offset -= sizeEndCentDir64Locator
    if offset < 0:
        # The file is not large enough to contain a ZIP64
        # end-of-archive record, so just return the end record we were given.
        return endrec
    fpin.seek(offset)
    data = fpin.read(sizeEndCentDir64Locator)
    if len(data) != sizeEndCentDir64Locator:
        raise OSError("Unknown I/O error")
    sig, diskno, reloff, disks = struct.unpack(structEndArchive64Locator, data)
    if sig != stringEndArchive64Locator:
        return endrec

    if diskno != 0 or disks > 1:
        raise BadZipFile("zipfiles that span multiple disks are not supported")

    offset -= sizeEndCentDir64
    if reloff > offset:
        raise BadZipFile("Corrupt zip64 end of central directory locator")
    # First, check the assumption that there is no prepended data.
    fpin.seek(reloff)
    extrasz = offset - reloff
    data = fpin.read(sizeEndCentDir64)
    if len(data) != sizeEndCentDir64:
        raise OSError("Unknown I/O error")
    if not data.startswith(stringEndArchive64) and reloff != offset:
        # Since we already have seen the Zip64 EOCD Locator, it's
        # possible we got here because there is prepended data.
        # Assume no 'zip64 extensible data'
        fpin.seek(offset)
        extrasz = 0
        data = fpin.read(sizeEndCentDir64)
        if len(data) != sizeEndCentDir64:
            raise OSError("Unknown I/O error")
    if not data.startswith(stringEndArchive64):
        raise BadZipFile("Zip64 end of central directory record not found")

    sig, sz, create_version, read_version, disk_num, disk_dir, \
        dircount, dircount2, dirsize, diroffset = \
        struct.unpack(structEndArchive64, data)
    if (diroffset + dirsize != reloff or
        sz + 12 != sizeEndCentDir64 + extrasz):
        raise BadZipFile("Corrupt zip64 end of central directory record")

    # Update the original endrec using data from the ZIP64 record
    endrec[_ECD_SIGNATURE] = sig
    endrec[_ECD_DISK_NUMBER] = disk_num
    endrec[_ECD_DISK_START] = disk_dir
    endrec[_ECD_ENTRIES_THIS_DISK] = dircount
    endrec[_ECD_ENTRIES_TOTAL] = dircount2
    endrec[_ECD_SIZE] = dirsize
    endrec[_ECD_OFFSET] = diroffset
    endrec[_ECD_LOCATION] = offset - extrasz
    return endrec


def _EndRecData(fpin):
    """Return data from the "End of Central Directory" record, or None.

    The data is a list of the nine items in the ZIP "End of central dir"
    record followed by a tenth item, the file seek offset of this record."""

    # Determine file size
    fpin.seek(0, 2)
    filesize = fpin.tell()

    # Check to see if this is ZIP file with no archive comment (the
    # "end of central directory" structure should be the last item in the
    # file if this is the case).
    try:
        fpin.seek(-sizeEndCentDir, 2)
    except OSError:
        return None
    data = fpin.read(sizeEndCentDir)
    if (len(data) == sizeEndCentDir and
        data[0:4] == stringEndArchive and
        data[-2:] == b"\000\000"):
        # the signature is correct and there's no comment, unpack structure
        endrec = struct.unpack(structEndArchive, data)
        endrec=list(endrec)

        # Append a blank comment and record start offset
        endrec.append(b"")
        endrec.append(filesize - sizeEndCentDir)

        # Try to read the "Zip64 end of central directory" structure
        return _EndRecData64(fpin, filesize - sizeEndCentDir, endrec)

    # Either this is not a ZIP file, or it is a ZIP file with an archive
    # comment.  Search the end of the file for the "end of central directory"
    # record signature. The comment is the last item in the ZIP file and may be
    # up to 64K long.  It is assumed that the "end of central directory" magic
    # number does not appear in the comment.
    maxCommentStart = max(filesize - ZIP_MAX_COMMENT - sizeEndCentDir, 0)
    fpin.seek(maxCommentStart, 0)
    data = fpin.read(ZIP_MAX_COMMENT + sizeEndCentDir)
    start = data.rfind(stringEndArchive)
    if start >= 0:
        # found the magic number; attempt to unpack and interpret
        recData = data[start:start+sizeEndCentDir]
        if len(recData) != sizeEndCentDir:
            # Zip file is corrupted.
            return None
        endrec = list(struct.unpack(structEndArchive, recData))
        commentSize = endrec[_ECD_COMMENT_SIZE] #as claimed by the zip file
        comment = data[start+sizeEndCentDir:start+sizeEndCentDir+commentSize]
        endrec.append(comment)
        endrec.append(maxCommentStart + start)

        # Try to read the "Zip64 end of central directory" structure
        return _EndRecData64(fpin, maxCommentStart + start, endrec)

    # Unable to find a valid end of central directory structure
    return None

def _sanitize_filename(filename):
    """Terminate the file name at the first null byte and
    ensure paths always use forward slashes as the directory separator."""

    # Terminate the file name at the first null byte.  Null bytes in file
    # names are used as tricks by viruses in archives.
    null_byte = filename.find(chr(0))
    if null_byte >= 0:
        filename = filename[0:null_byte]
    # This is used to ensure paths in generated ZIP files always use
    # forward slashes as the directory separator, as required by the
    # ZIP format specification.
    if os.sep != "/" and os.sep in filename:
        filename = filename.replace(os.sep, "/")
    if os.altsep and os.altsep != "/" and os.altsep in filename:
        filename = filename.replace(os.altsep, "/")
    return filename


class ZipInfo:
    """Class with attributes describing each file in the ZIP archive."""

    __slots__ = (
        'orig_filename',
        'filename',
        'date_time',
        'compress_type',
        'compress_level',
        'comment',
        'extra',
        'create_system',
        'create_version',
        'extract_version',
        'reserved',
        'flag_bits',
        'volume',
        'internal_attr',
        'external_attr',
        'header_offset',
        'CRC',
        'compress_size',
        'file_size',
        '_raw_time',
        '_end_offset',
    )

    def __init__(self, filename="NoName", date_time=(1980,1,1,0,0,0)):
        self.orig_filename = filename   # Original file name in archive

        # Terminate the file name at the first null byte and
        # ensure paths always use forward slashes as the directory separator.
        filename = _sanitize_filename(filename)

        self.filename = filename        # Normalized file name
        self.date_time = date_time      # year, month, day, hour, min, sec

        if date_time[0] < 1980:
            raise ValueError('ZIP does not support timestamps before 1980')

        # Standard values:
        self.compress_type = ZIP_STORED # Type of compression for the file
        self.compress_level = None      # Level for the compressor
        self.comment = b""              # Comment for each file
        self.extra = b""                # ZIP extra data
        if sys.platform == 'win32':
            self.create_system = 0          # System which created ZIP archive
        else:
            # Assume everything else is unix-y
            self.create_system = 3          # System which created ZIP archive
        self.create_version = DEFAULT_VERSION  # Version which created ZIP archive
        self.extract_version = DEFAULT_VERSION # Version needed to extract archive
        self.reserved = 0               # Must be zero
        self.flag_bits = 0              # ZIP flag bits
        self.volume = 0                 # Volume number of file header
        self.internal_attr = 0          # Internal attributes
        self.external_attr = 0          # External file attributes
