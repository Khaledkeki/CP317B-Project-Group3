"""Run python -m app.create_staff to provision one staff account locally."""

from getpass import getpass

from app.auth import RegistrationError, register_user
from app.database import DATABASE_PATH, get_connection, initialize_database


def main():
    initialize_database()
    with get_connection() as connection:
        if connection.execute("SELECT 1 FROM users WHERE role = 'staff'").fetchone():
            print("A staff account already exists. No changes made.")
            return
    print("Create the local prototype's staff account. No default password is provided.")
    name = input("Full name: ")
    email = input("Email: ")
    username = input("Username: ")
    password = getpass("Password (8+ characters, up to 72 UTF-8 bytes): ")
    if password != getpass("Confirm password: "):
        raise SystemExit("Passwords do not match. No account created.")
    try:
        register_user(DATABASE_PATH, name, email, username, password, role="staff")
    except RegistrationError as error:
        raise SystemExit(" ".join(error.errors.values())) from None
    print("Staff account created. Sign in through the website.")


if __name__ == "__main__":
    main()
