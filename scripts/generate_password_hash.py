from getpass import getpass

from argon2 import PasswordHasher


password = getpass("New shared application password: ")
confirmation = getpass("Confirm password: ")
if not password or password != confirmation:
    raise SystemExit("Passwords did not match or were empty.")

hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, hash_len=32, salt_len=16)
print("Replace APPLICATION_PASSWORD_HASH in app/config.py with:")
print(hasher.hash(password))
