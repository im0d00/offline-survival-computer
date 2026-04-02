#!/usr/bin/env python3
"""
Helper script to generate admin credentials for the Offline Survival Computer.
Run this script to generate a password hash that can be added to .env
"""
import getpass
from werkzeug.security import generate_password_hash

def main():
    print("=== Offline Survival Computer - Admin Credential Generator ===\n")

    username = input("Enter admin username: ").strip()
    if not username:
        print("Error: Username cannot be empty.")
        return

    password = getpass.getpass("Enter admin password: ")
    if not password:
        print("Error: Password cannot be empty.")
        return

    password_confirm = getpass.getpass("Confirm admin password: ")
    if password != password_confirm:
        print("Error: Passwords do not match.")
        return

    # Generate password hash using pbkdf2:sha256
    password_hash = generate_password_hash(password, method='pbkdf2:sha256')

    print("\n" + "="*60)
    print("SUCCESS! Add these lines to your .env file:")
    print("="*60)
    print(f"ADMIN_USERNAME={username}")
    print(f"ADMIN_PASSWORD_HASH={password_hash}")
    print("="*60)
    print("\nMake sure to keep your .env file secure and never commit it to version control!")

if __name__ == "__main__":
    main()
