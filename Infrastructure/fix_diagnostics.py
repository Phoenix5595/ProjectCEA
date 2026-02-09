import sys
import json
import subprocess

def get_diagnostics(file_path):
    cmd = f"pyright {file_path} --outputjson"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    try:
        data = json.loads(result.stdout)
        return data.get('generalDiagnostics', [])
    except json.JSONDecodeError:
        print(f"Failed to decode JSON from pyright for {file_path}")
        return []

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 fix_diagnostics.py <file_path>")
        sys.exit(1)
    
    file_path = sys.argv[1]
    diagnostics = get_diagnostics(file_path)
    print(json.dumps(diagnostics, indent=2))
