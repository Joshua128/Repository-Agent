import hashlib

def hash_file(file_path):
    sha_hash = hashlib.sha256()

    with open(file_path, 'rb') as f:
        while chunk := f.read(64 * 1024):
            sha_hash.update(chunk)

    return sha_hash.hexdigest()