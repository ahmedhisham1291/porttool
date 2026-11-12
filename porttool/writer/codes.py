"""Stable finding codes of the writer (prefix ``WRT-``). Listed in HANDOFF.md."""

from __future__ import annotations

UNSUPPORTED = "WRT-UNSUPPORTED-001"  # operation or variant not supported yet
REF_MISSING = "WRT-REF-001"  # a referenced object does not exist
REF_KIND = "WRT-REF-002"  # the referenced object cannot be used that way
INCOMPLETE = "WRT-REQ-001"  # the request lacks a value the new element needs
NAME_USED = "WRT-NAME-001"  # the short name is taken by a different object
OWNER = "WRT-OWNER-001"  # the target SWC is model-owned or locked
NO_BEHAVIOR = "WRT-BEHAVIOR-001"  # the SWC has no internal behavior
STATE_CHANGED = "WRT-STATE-001"  # file changed since the model was loaded (dry run only)
STATE_UNKNOWN = "WRT-STATE-002"  # file is not part of the loaded model or cannot be read
PARSE = "WRT-PARSE-001"  # file cannot be indexed
LOCATE = "WRT-LOCATE-001"  # an object of the model was not found in its file
PLACE = "WRT-PLACE-001"  # no file found for a new object
FILE_NAME = "WRT-FILE-002"  # the name of a new object cannot be used as a file name
FILE_COLLISION = "WRT-FILE-001"  # a new file would collide with an existing file name
READ_ONLY = "WRT-READONLY-001"  # target file is read-only
LOCKED = "WRT-LOCKED-001"  # target file is held open by another program
WRITE = "WRT-WRITE-001"  # any other failure to write a file
NOT_WELL_FORMED = "WRT-XML-001"  # the result would not be well-formed XML
XSD_INVALID = "WRT-XSD-001"  # the result does not validate against the configured XSD
XSD_UNUSABLE = "WRT-XSD-002"  # the configured XSD cannot be loaded
ABORTED = "WRT-ABORT-001"  # apply stopped before every file was written
DEPENDS = "WRT-DEP-001"  # operation dropped because one of its files was refused
SETTING = "WRT-SETTING-001"  # a [writer] setting is wrong, the default is used
ORDER_UNKNOWN = "WRT-ORDER-001"  # position of a new container is uncertain (unknown sibling)
DCF_NOT_UPDATED = "WRT-DCF-001"  # new file was not registered in the .dcf
VALUE_IGNORED = "WRT-VALUE-001"  # part of the request was ignored
DELTA_WRITE = "WRT-DELTA-001"  # the delta file could not be written
DELTA_SPLIT = "WRT-DELTA-002"  # delta holds one part of an element that is split over files
INFO_EXISTS = "WRT-INFO-EXISTS"  # the object already exists as requested: nothing to do
INFO_EVENT = "WRT-INFO-EVENT"  # an RTE event was created; task mapping is left to the pipeline
INFO_DELTA = "WRT-INFO-DELTA"  # delta file written
