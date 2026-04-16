import subprocess
import sys

try:
    result = subprocess.run(
        ["uvx", "--from", "youtube-transcript-api", "youtube_transcript_api", "BL4co1RzpUE", "--languages", "bn", "hi", "en"],
        capture_output=True,
        text=False
    )
    with open('transcript.txt', 'wb') as f:
        f.write(result.stdout)
    print("Saved to transcript.txt")
    if result.stderr:
        print("Stderr:", result.stderr.decode('utf-8', errors='ignore'))
except Exception as e:
    print(f"Error: {e}")
