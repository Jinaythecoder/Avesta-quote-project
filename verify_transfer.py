#!/usr/bin/env python3
"""Private backend-only, TEST-environment Avesta XLSX transfer and round-trip verification.
Requires approved template mounted at AVESTA_TEMPLATE_PATH and Railway sealed secrets.
Never store the template or service-role key in a public Git repository.
"""
import hashlib, io, json, os, sys, urllib.parse, urllib.request, urllib.error, zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

PROJECT = "vbpxcltdamyvmgxzrtwc"
FUNCTION = "avesta-secure-xlsx-transfer"
MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ENQUIRY = "TEST:20261015:A0001"
REF = "ASA/0001/D/26-27"
MASTER = Path(os.environ.get("AVESTA_TEMPLATE_PATH", "AVESTA QUOTE FORMAT.xlsx"))
DOWNLOAD = Path(os.environ.get("AVESTA_DOWNLOADED_PATH", "/tmp/avesta_test_roundtrip.xlsx"))

def sha(x): return hashlib.sha256(x).hexdigest()

def shape(blob):
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        corrupt = z.testzip()
        if corrupt: raise ValueError("Corrupt XLSX member: " + corrupt)
        names = sorted(z.namelist())
        if "xl/workbook.xml" not in names or "xl/styles.xml" not in names: raise ValueError("Incomplete XLSX")
        ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        workbook = ET.fromstring(z.read("xl/workbook.xml"))
        sheets = [e.attrib["name"] for e in workbook.findall(".//m:sheets/m:sheet", ns)]
        def detail(path):
            root = ET.fromstring(z.read(path))
            return {
                "merges": sorted(e.attrib["ref"] for e in root.findall(".//m:mergeCell", ns)),
                "formulas": [(e.attrib.get("r"),e.text) for e in root.findall(".//m:f", ns)],
                "cells": [ET.tostring(e, encoding="unicode") for e in root.findall(".//m:c", ns)],
                "columns": [e.attrib for e in root.findall(".//m:col", ns)],
                "rows": [e.attrib for e in root.findall(".//m:row", ns)],
                "page_setup": [e.attrib for e in root.findall(".//m:pageSetup", ns)],
                "page_margins": [e.attrib for e in root.findall(".//m:pageMargins", ns)],
            }
        return {
            "sheets": sheets,
            "worksheets": {n:detail(n) for n in names if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")},
            "styles_sha": sha(z.read("xl/styles.xml")),
            "branding_sha": {n:sha(z.read(n)) for n in names if n.startswith("xl/media/")},
            "all_members_sha": {n:sha(z.read(n)) for n in names},
        }

def call(url, key, method, body=None, content_type=None):
    headers = {"Authorization":"Bearer " + key, "apikey":key}
    if content_type: headers["Content-Type"] = content_type
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.status, response.read()

def main():
    url = os.environ.get("SUPABASE_URL","").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY","")
    if url != "https://" + PROJECT + ".supabase.co" or not key:
        print("BLOCKED: configure correct SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in Railway private variables", file=sys.stderr)
        return 2
    if not MASTER.is_file():
        print("BLOCKED: approved XLSX missing from secure backend at AVESTA_TEMPLATE_PATH", file=sys.stderr)
        return 2
    original = MASTER.read_bytes()
    if len(original) > 10 * 1024 * 1024: raise ValueError("Template exceeds private bucket limit")
    before = shape(original)
    # Register the isolated test enquiry using the existing RPC, no production sequence touched.
    payload = {
        "p_environment":"test", "p_enquiry_id":ENQUIRY,
        "p_account":"TEST-ONLY@example.invalid", "p_message_id":"TEST-ONLY-A0001",
        "p_received_at":"2026-10-15T03:00:00Z",
        "p_subject":"TEST ONLY approved Avesta master transfer",
        "p_body":"Controlled internal storage test only",
        "p_requirements":{"test_only":True}, "p_customer":{}, "p_pr":"AS PER THE EMAIL"
    }
    rpc = url + "/rest/v1/rpc/avesta_save_enquiry"
    call(rpc,key,"POST",json.dumps(payload).encode(),"application/json")
    args = urllib.parse.urlencode({"environment":"test","email":"1","enquiry_id":ENQUIRY,"ref":REF})
    endpoint = url + "/functions/v1/" + FUNCTION + "?" + args
    _, data = call(endpoint,key,"POST",original,MIME)
    ack = json.loads(data)
    if not ack.get("success"): raise RuntimeError("Storage endpoint did not acknowledge successful upload")
    _, result = call(endpoint,key,"GET")
    DOWNLOAD.parent.mkdir(parents=True,exist_ok=True)
    DOWNLOAD.write_bytes(result)
    expected=sha(original)
    verified=(expected==sha(result)==ack.get("sha256") and original==result and before==shape(result))
    evidence={
        "test_only":True,"bucket":ack.get("bucket"),"path":ack.get("path"),
        "original_sha256":expected,"downloaded_sha256":sha(result),
        "bytes_original":len(original),"bytes_downloaded":len(result),
        "byte_identical":original==result,"structure_identical":before==shape(result),
        "transfer_verified":verified
    }
    print(json.dumps(evidence,sort_keys=True))
    if not verified: return 1
    # Separate SQL connector read-back is required: internal schema is not a REST exposed schema.
    print("PASS: transfer and workbook integrity; verify enquiry_processing record independently before READY")
    return 0

if __name__=="__main__":
    try: sys.exit(main())
    except urllib.error.HTTPError as ex:
        print("TRANSFER FAILED: HTTP "+str(ex.code)+" (body withheld to protect secrets)",file=sys.stderr)
        sys.exit(1)
    except Exception as ex:
        print("TRANSFER FAILED: "+type(ex).__name__,file=sys.stderr)
        sys.exit(1)
