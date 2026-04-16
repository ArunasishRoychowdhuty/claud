from youtube_transcript_api import YouTubeTranscriptApi
import json

try:
    transcript = YouTubeTranscriptApi.get_transcript('BL4co1RzpUE', languages=['bn', 'hi', 'en'])
    with open('transcript.json', 'w', encoding='utf-8') as f:
        json.dump(transcript, f, ensure_ascii=False, indent=2)
    print("Saved transcript.json")
except Exception as e:
    print(f"Error: {e}")
