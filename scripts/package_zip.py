import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_FILENAME = "meeting-minutes-generator.zip"

EXCLUDE_DIRS = {
    "__pycache__",
    "downloads",
    ".git",
    ".vscode",
    ".idea",
    "venv",
    ".streamlit",
    "frontend/node_modules",
    "frontend/dist",
}

EXCLUDE_FILES = {
    OUTPUT_FILENAME,
    ".DS_Store",
}


def should_exclude(rel_path: str) -> bool:
    parts = rel_path.split(os.sep)
    for p in parts:
        if p in EXCLUDE_DIRS:
            return True
    if os.path.basename(rel_path) in EXCLUDE_FILES:
        return True
    return False


def package_app():
    out_path = os.path.join(ROOT, OUTPUT_FILENAME)
    print(f"Creating {out_path}...")
    count = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(ROOT):
            rel_dir = os.path.relpath(dirpath, ROOT)
            if rel_dir == ".":
                rel_dir = ""
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
            for name in filenames:
                file_path = os.path.join(dirpath, name)
                arcname = os.path.relpath(file_path, ROOT)
                if should_exclude(arcname):
                    continue
                if name.endswith(".pyc"):
                    continue
                print(f"  Adding: {arcname}")
                zf.write(file_path, arcname)
                count += 1
    print("-" * 30)
    print(f"Success! Added {count} files to {out_path}")


if __name__ == "__main__":
    package_app()
