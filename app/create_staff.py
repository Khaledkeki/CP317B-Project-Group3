"""Run python -m app.create_staff [--role manager] to provision one local account."""

import argparse
from getpass import getpass

from app.auth import RegistrationError, register_user
from app.database import DATABASE_PATH, get_connection, initialize_database


def main():
    parser = argparse.ArgumentParser(description="Create one local prototype account.")
    parser.add_argument("--role", choices=("staff", "manager"), default="staff")
    args = parser.parse_args()
    initialize_database()
    with get_connection() as connection:
        if connection.execute("SELECT 1 FROM users WHERE role = ?", (args.role,)).fetchone():
            print(f"A {args.role} account already exists. No changes made.")
            return
    print(f"Create the local prototype's {args.role} account. No default password is provided.")
    name = input("Full name: ")
    email = input("Email: ")
    username = input("Username: ")
    password = getpass("Password (8+ characters, up to 72 UTF-8 bytes): ")
    if password != getpass("Confirm password: "):
        raise SystemExit("Passwords do not match. No account created.")
    try:
        register_user(DATABASE_PATH, name, email, username, password, role=args.role)
    except RegistrationError as error:
        raise SystemExit(" ".join(error.errors.values())) from None
    print(f"{args.role.capitalize()} account created. Sign in through the website.")


if __name__ == "__main__":
    main()
