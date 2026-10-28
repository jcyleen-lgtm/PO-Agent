"""
Run this on your own computer to create the login settings:

    python make_password_hash.py

It asks for a password (typing is hidden), then prints APP_PASSWORD_HASH and a new SESSION_SECRET.
Copy those lines into .env (local) and into Vercel → Settings → Environment Variables (production).
The plain password is never saved anywhere.
"""

import getpass
import secrets

from auth import hash_password

if __name__ == "__main__":
    while True:
        pw = getpass.getpass("New password (min 8 characters): ")
        if len(pw) < 8:
            print("Too short, try again.")
            continue
        if getpass.getpass("Type it again: ") != pw:
            print("Passwords don't match, try again.")
            continue
        break

    print("\nAdd these to .env and to Vercel Environment Variables:\n")
    print(f"APP_PASSWORD_HASH={hash_password(pw)}")
    print(f"SESSION_SECRET={secrets.token_urlsafe(48)}")
    print("\nAlso set APP_USERNAME=<your username>")
