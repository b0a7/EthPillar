#!/usr/bin/env python3
import sys
import os
import subprocess
import hashlib

CACHE_DIR = "/ethpillar/tests/integration/cache"

def get_extracted_cache_key(archive_path: str, dest_dir: str) -> str:
    hasher = hashlib.md5()
    with open(archive_path, 'rb') as f:
        # Hash the first 1MB to be fast but reasonably unique
        buf = f.read(1024 * 1024)
        hasher.update(buf)
    hasher.update(dest_dir.encode('utf-8'))
    return hasher.hexdigest()

def main():
    if len(sys.argv) < 2:
        sys.exit(1)
        
    cmd_type = sys.argv[1] # "tar" or "unzip"
    args = sys.argv[2:]
    
    real_bin = f"/usr/bin/{cmd_type}"
    cmd = [real_bin] + args
    
    archive_path = None
    dest_dir = None
    
    if cmd_type == "tar":
        for i, arg in enumerate(args):
            if arg.endswith(".tar.gz") or arg.endswith(".tar.xz"):
                archive_path = arg
            elif arg == "-C" and i + 1 < len(args):
                dest_dir = args[i + 1]
    elif cmd_type == "unzip":
        for i, arg in enumerate(args):
            if arg.endswith(".zip"):
                archive_path = arg
            elif arg == "-d" and i + 1 < len(args):
                dest_dir = args[i + 1]
                
    if not archive_path or not dest_dir or not os.path.exists(archive_path):
        os.execv(real_bin, cmd)
        
    # Blacklist system directories where many binaries share the same folder
    if dest_dir in ["/usr/local/bin", "/usr/bin", "/bin", "/usr/local/bin/", "/usr/bin/", "/bin/"]:
        os.execv(real_bin, cmd)
        
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_key = get_extracted_cache_key(archive_path, dest_dir)
    cache_tar = os.path.join(CACHE_DIR, f"extracted_{cache_key}.tar")
    
    if os.path.exists(cache_tar) and os.path.getsize(cache_tar) > 0:
        print(f"[EXTRACT CACHE] Hit for {os.path.basename(archive_path)}")
        os.makedirs(dest_dir, exist_ok=True)
        res = subprocess.run(["sudo", "/usr/bin/tar", "xf", cache_tar, "-C", dest_dir])
        sys.exit(res.returncode)
        
    print(f"[EXTRACT CACHE] Miss for {os.path.basename(archive_path)}. Extracting...")
    res = subprocess.run(cmd)
    
    if res.returncode == 0 and os.path.exists(dest_dir):
        print(f"[EXTRACT CACHE] Caching extracted files...")
        temp_tar = os.path.join(CACHE_DIR, f"tmp_{cache_key}.tar")
        # sudo to ensure we can read all extracted files (which might be root-owned)
        subprocess.run(["sudo", "/usr/bin/tar", "cf", temp_tar, "-C", dest_dir, "."], check=False)
        # Ensure the cache file is readable by the host user
        subprocess.run(["sudo", "chmod", "666", temp_tar], check=False)
        try:
            os.rename(temp_tar, cache_tar)
        except OSError:
            subprocess.run(["sudo", "rm", "-f", temp_tar], check=False)
            
    sys.exit(res.returncode)

if __name__ == "__main__":
    main()
