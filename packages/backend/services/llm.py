import json
from google import genai
from google.genai import types

class GeminiAssistant:
    def __init__(self, api_key: str, model_name: str = "gemini-2.5-flash"):
        # Initialize the new SDK Client
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name

    async def generate_converse_queue(self, logs: list[str]) -> list[dict]:
        """Passes logs to Gemini and returns a parsed JSON array of questions/suggestions."""
        logs_text = "\n".join(logs) if logs else "No logs yet."
        
        prompt = f"""Read the travel logs given below and then do the tasks:
LOGS:
{logs_text}

TASK:
The logs would be used to write blog posts, and make documentary reels on social media. Your job is to be a curious reader, and come up with questions that will make a more compelling blog post and/or a better documentary reel. Come up with a maximum of 5 questions and 3 suggestions, and based on your intelligence score them on the importance of their answers on a scale of 0 to 3. 
If some key information is missing and is essential for you to write the blog post or make the documentary, then label it as 3. If something is really not important and you are just conversing, then label it 0. So, 3 is when it is a deal breaker and you can not work without that input, 2 is when it is very important, 1 is when it is important and would be used to make reading the blog post more interesting, adding finer details, etc. 0 when it is not important. 

Your suggestions should entail what kind of videos the traveller can take, or experience they can have. You can even ask. Eg: You can ask, "Did you take a video while travelling from the airport to the jungle?" or "You should take videos of the surroundings, travelling..".

Know the scope of the blog post and of the documentary:
The blog post and the documentary would cover the travel, the accommodation, the local guide, the arranging company, the species observed including the target species, their quirks, experience of the exploration of the jungle, finances, to do's, precautions, etc. 

MANDATORY Response Format: 
[
    {{
        "message_type": "question",
        "importance": 3,
        "text": "What kind of amenities are you getting in the homestay?"
    }}
]"""

        try:
            # Use the native async client (client.aio) and the new types module for config
            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                )
            )
            
            queue = json.loads(response.text)
            
            # Ensure it is a valid list of dictionaries
            if isinstance(queue, list) and len(queue) > 0 and isinstance(queue[0], dict):
                # Sort the queue by importance (highest first) so the 3s get asked right away!
                queue.sort(key=lambda x: x.get("importance", 0), reverse=True)
                return queue
            else:
                raise ValueError("Response was not a valid list of objects")
                
        except Exception as e:
            print(f"JSON Parse Error: {e}")
            # Fallback in case of an error
            return [{
                "message_type": "question",
                "importance": 3,
                "text": "Can you describe the environment in more detail?"
            }]

    async def generate_description(self, image_bytes: bytes, mime_type: str) -> str:
        prompt = "Analyze this media and provide a concise, detailed description suitable for a field research log or documentary archive."
        
        # Use the new Part.from_bytes method for media
        media_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        
        response = await self.client.aio.models.generate_content(
            model=self.model_name,
            contents=[media_part, prompt]
        )
        return response.text

    async def generate_transcription(self, audio_bytes: bytes, mime_type: str) -> str:
        prompt = "Please provide a highly accurate, word-for-word transcription of the speech in this audio/video file."
        
        media_part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)
        
        response = await self.client.aio.models.generate_content(
            model=self.model_name,
            contents=[media_part, prompt]
        )
        return response.text