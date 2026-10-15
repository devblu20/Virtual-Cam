"""Create an ignored server configuration file; never print the password or hash."""
import getpass
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from website_login import password_hash

if __name__ == "__main__":
    username = input("Website login ID: ").strip()
    if not username or len(username) > 80 or any(c in username for c in "\n\r#=\"'"):
        raise SystemExit("Enter a valid login ID.")
    password = getpass.getpass("Password: ")
    if not password or password != getpass.getpass("Confirm password: "):
        raise SystemExit("Passwords must match and cannot be empty.")
    path = Path(__file__).resolve().parents[1] / ".env.website-login"
    with path.open("x", encoding="utf-8") as output:
        output.write(f"VCAM_LOGIN_ID={username}\nVCAM_LOGIN_PASSWORD_HASH='{password_hash(password)}'\n")
    print("Saved .env.website-login. Set these two variables in Railway, then deploy. Keep this file private.")
