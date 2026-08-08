"""Gen VIII (Sword/Shield) — Switch ROM, same 0x2983/ROL3 text cipher.

Container: Switch ROM (.nsp/.xci). Text lives in .dat/.tbl files, not GARCs.
Key base: 0x7C89 (fixed, not entry-derived like 3DS/DS).
Cipher core: identical — XOR with ROL3-advanced key, MULT 0x2983.

xoleon decodes the text once we have a Switch container reader.
"""

# TBD: Switch ROM support not yet implemented
_GEN8_SWSH = {}
