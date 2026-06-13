export interface User {
  id: number;
  telegram_user_id: number;
  username: string | null;
  first_name: string | null;
  last_name: string | null;
  created_at: string;
}

export interface Media {
  id: number;
  message_id: number;
  media_type: string;
  file_path: string | null;
  file_name: string | null;
  file_size: number | null;
  latitude: number | null;
  longitude: number | null;
  description: string | null;
  transcription: string | null;
}

export interface Message {
  id: number;
  telegram_message_id: number;
  text: string | null;
  question: string | null;
  timestamp: string;
  user: User | null;
  media: Media[];
}

export interface PaginatedMessages {
  messages: Message[];
  total_count: number;
  total_pages: number;
  current_page: number;
}

export interface ExportMessage {
  timestamp: string;
  user: string;
  text: string | null;
  image_description: string | null;
  audio_transcription: string | null;
  location: string | null;
}
