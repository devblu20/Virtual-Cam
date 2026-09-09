"""Generate an individual invite key locally. Never run in public CI logs."""
import argparse
import hashlib
import json
import re
import secrets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("user_id", help="Non-personal identifier, e.g. tester-01")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.user_id):
        parser.error("Use 1-64 letters, numbers, hyphens or underscores.")
    key = secrets.token_urlsafe(32)
    digest = hashlib.sha256(key.encode()).hexdigest()
    print("PRIVATE access key for this user (share privately; do not commit):")
    print(key)
    print("VCAM_ACCESS_KEYS entry (merge with existing entries to keep other users):")
    print(json.dumps({args.user_id: digest}))


if __name__ == "__main__":
    main()
