import zipfile
import os

OUTPUT_FILENAME = 'meeting-minutes-generator.zip'

EXCLUDE_DIRS = {
    '__pycache__',
    'downloads',
    '.git',
    '.vscode',
    '.idea',
    'venv',
    '.streamlit',
    'frontend/node_modules',
    'frontend/dist',
}

EXCLUDE_FILES = {
    OUTPUT_FILENAME,
    '.DS_Store'
}

def should_exclude(path):
    # Check if any part of the path is in EXCLUDE_DIRS
    parts = path.split(os.sep)
    for p in parts:
        if p in EXCLUDE_DIRS:
            return True
    
    # Check specific file exclusions
    if os.path.basename(path) in EXCLUDE_FILES:
        return True
        
    return False

def package_app():
    print(f"Creating {OUTPUT_FILENAME}...")
    
    count = 0
    with zipfile.ZipFile(OUTPUT_FILENAME, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk('.'):
            # Modify dirs in-place to prune traversal
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            
            for file in files:
                file_path = os.path.join(root, file)
                
                # Check exclusion logic again for safety
                if should_exclude(file_path):
                    continue
                    
                if file.endswith('.pyc'):
                    continue

                # Add to zip
                print(f"  Adding: {file_path}")
                zf.write(file_path)
                count += 1
                
    print("-" * 30)
    print(f"Success! Added {count} files to {OUTPUT_FILENAME}")

if __name__ == "__main__":
    package_app()
