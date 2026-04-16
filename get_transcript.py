import sys
import json
from youtube_transcript_api import YouTubeTranscriptApi

try:
    transcript_list = YouTubeTranscriptApi.list_transcripts('BL4co1RzpUE')
    transcript = transcript_list.find_transcript(['bn', 'en', 'hi', 'ur'])
    
    with open('transcript.txt', 'w', encoding='utf-8') as f:
        json.dump(transcript.fetch(), f, ensure_ascii=False, indent=2)
    print("Transcript saved to transcript.txt")
except Exception as e:
    print(f"Error: {e}")
