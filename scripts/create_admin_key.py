"""Run locally: prints a NEW administrator credential and its Railway hash.
Keep the raw credential in a password manager. Never commit or share it.
"""
import hashlib
import secrets

if __name__ == "__main__":
    key = secrets.token_urlsafe(48)
    print("Administrator key (keep private, do NOT put in Railway or extension):")
    print(key)
    print("\nRailway variable (hash only):")
    print("VCAM_ADMIN_KEY_SHA256=" + hashlib.sha256(key.encode()).hexdigest())
