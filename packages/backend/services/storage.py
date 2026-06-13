import os
import pathlib
import aiofiles

class LocalFileManager:
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    async def save_file(self, file_bytes: bytes, user_id: int, message_id: int, media_type: str, file_id: str, default_ext: str, original_path: str = None) -> dict:
        """
        Saves raw bytes to the local disk and returns the metadata dict.
        """
        ext = pathlib.Path(original_path).suffix if original_path else default_ext
        if not ext:
            ext = default_ext
            
        file_name = f"{media_type}_{file_id}{ext}"
        
        # Create user/message directory structure
        relative_folder = os.path.join(str(user_id), str(message_id))
        absolute_folder = os.path.join(self.base_dir, relative_folder)
        os.makedirs(absolute_folder, exist_ok=True)
        
        relative_path = os.path.join(relative_folder, file_name)
        absolute_path = os.path.join(absolute_folder, file_name)
        
        # Write bytes asynchronously
        async with aiofiles.open(absolute_path, 'wb') as f:
            await f.write(file_bytes)
            
        return {
            "file_name": file_name,
            "file_path": relative_path, # We store the relative path in the DB
            "file_size": len(file_bytes)
        }