#!/usr/bin/env python3
"""Stands in for prep/kopf_aufsetzen.py: VORLAGE.blend KOPF.glb AUS.blend. Writes the template plus the head's bytes;
a head that contains b"nohead" fails like a mesh without a neck."""
import sys
template, head, out = sys.argv[1:4]
data = open(head, "rb").read()
if b"nohead" in data:
    sys.exit("Kein Hals gefunden")
open(out, "wb").write(open(template, "rb").read() + b" kopf:" + data[4:])
print("Massstab 0.095")
