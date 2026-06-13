import { useState, useEffect, useRef, type ChangeEvent } from 'react'
import type { Message, Media, User, PaginatedMessages, ExportMessage } from './types' // Import ExportMessage

// Use the VITE_API_URL from environment, fall back to localhost
const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'
const MESSAGES_PER_PAGE = 25;

// --- Reusable Editable Field Component (Unchanged) ---
interface EditableFieldProps {
  media: Media;
  fieldName: 'description' | 'transcription';
  onUpdate: (updatedMedia: Media) => void;
}

function EditableField({ media, fieldName, onUpdate }: EditableFieldProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [text, setText] = useState(media[fieldName] || "");
  const [isGenerating, setIsGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");

  const label = fieldName.charAt(0).toUpperCase() + fieldName.slice(1);
  const hasText = text !== null && text.length > 0;

  useEffect(() => {
    setText(media[fieldName] || "");
  }, [media, fieldName]);

  const handleSave = async () => {
    try {
      const response = await fetch(`${API_URL}/media/${media.id}/${fieldName}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [fieldName]: text }),
      });
      if (!response.ok) throw new Error('Failed to save');
      const updatedMedia: Media = await response.json();
      onUpdate(updatedMedia);
      setIsEditing(false);
      setError(null);
    } catch (err: any) {
      setError(err.message);
    }
  };

  const handleGenerate = async () => {
    setIsGenerating(true);
    setError(null);
    try {
      const response = await fetch(`${API_URL}/media/${media.id}/generate-${fieldName}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: prompt }),
      });
      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'Failed to generate');
      }
      const updatedMedia: Media = await response.json();
      onUpdate(updatedMedia);
      setPrompt("");
    } catch (err: any) {
      setError(err.message);
    } finally {
      setIsGenerating(false);
    }
  };

  return (
    <div className="bg-gray-50 p-3 rounded-lg border">
      <label className="block text-sm font-medium text-gray-700 mb-1">{label}</label>
      {error && <div className="text-sm text-red-500 mb-2">Error: {error}</div>}
      
      {isEditing ? (
        <div className="flex flex-col gap-2">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            className="w-full p-2 border border-gray-300 rounded-md shadow-sm text-sm"
            rows={3}
          />
          <div className="flex gap-2">
            <button
              onClick={handleSave}
              className="px-3 py-1 bg-blue-600 text-white rounded-md text-sm hover:bg-blue-700"
            >
              Save
            </button>
            <button
              onClick={() => setIsEditing(false)}
              className="px-3 py-1 bg-gray-300 text-gray-800 rounded-md text-sm hover:bg-gray-400"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <p className="text-sm text-gray-800 whitespace-pre-wrap flex-1">
            {hasText ? text : <i className="text-gray-400">No {fieldName} yet.</i>}
          </p>
          <div className="flex items-center gap-2">
            {!hasText && (
              <>
                <input
                  type="text"
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  placeholder="Optional: Custom prompt..."
                  className="flex-1 p-2 border border-gray-300 rounded-md shadow-sm text-sm"
                />
                <button
                  onClick={handleGenerate}
                  disabled={isGenerating}
                  className="px-3 py-2 bg-green-600 text-white rounded-md text-sm hover:bg-green-700 disabled:bg-gray-400"
                >
                  {isGenerating ? '...' : 'Generate'}
                </button>
              </>
            )}
            {hasText && (
              <button
                onClick={() => setIsEditing(true)}
                className="px-3 py-1 bg-gray-300 text-gray-800 rounded-md text-sm hover:bg-gray-400"
              >
                Edit
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}


// --- MediaItem Component (Unchanged) ---
function MediaItem({ media, onUpdate }: { media: Media; onUpdate: (updatedMedia: Media) => void; }) {
  const [url, setUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchMediaUrl = async () => {
      if (media.media_type === 'location') {
        setUrl(`http://googleusercontent.com/maps/google.com/0{media.latitude},${media.longitude}`);
        setLoading(false);
        return;
      }
      if (!media.file_path) {
        setLoading(false);
        return;
      }
      try {
        const response = await fetch(`${API_URL}/media-url?key=${encodeURIComponent(media.file_path)}`);
        const data = await response.json();
        if (response.ok) setUrl(data.url);
      } catch (error) {
        console.error("Failed to fetch media URL", error);
      } finally {
        setLoading(false);
      }
    };
    fetchMediaUrl();
  }, [media.file_path, media.latitude, media.longitude, media.media_type]);

  const renderMedia = () => {
    if (loading) return <div className="text-sm text-gray-500 italic animate-pulse">Loading media...</div>;
    if (!url) return <div className="text-sm text-red-500">Could not load media.</div>;

    switch (media.media_type) {
      case 'photo':
      case 'sticker':
        return <img src={url} alt={media.file_name || 'photo'} className="max-w-full h-auto rounded-lg border border-gray-200" />;
      case 'video':
        return <video controls src={url} className="max-w-full rounded-lg border border-gray-200" />;
      case 'audio':
      case 'voice':
        return <audio controls src={url} className="w-full" />;
      case 'location':
        return <a href={url} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline">View Location ({media.latitude}, {media.longitude})</a>;
      default:
        return <a href={url} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline">Download {media.file_name || media.media_type}</a>;
    }
  }

  return (
    <div className="flex flex-col gap-2">
      {renderMedia()}
      {['photo', 'sticker'].includes(media.media_type) && (
        <EditableField media={media} fieldName="description" onUpdate={onUpdate} />
      )}
      {['audio', 'voice'].includes(media.media_type) && (
        <EditableField media={media} fieldName="transcription" onUpdate={onUpdate} />
      )}
    </div>
  )
}

// --- Lazy Loading Component (Unchanged) ---
function LazyMediaItem({ media, onUpdate }: { media: Media; onUpdate: (updatedMedia: Media) => void; }) {
  const [isVisible, setIsVisible] = useState(false);
  const placeholderRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setIsVisible(true);
          observer.unobserve(entry.target);
        }
      },
      { rootMargin: "0px 0px 200px 0px" } 
    );

    const currentRef = placeholderRef.current;
    if (currentRef) {
      observer.observe(currentRef);
    }

    return () => {
      if (currentRef) {
        observer.unobserve(currentRef);
      }
    };
  }, []);

  return (
    <div ref={placeholderRef} style={{ minHeight: '100px' }}>
      {isVisible ? (
        <MediaItem media={media} onUpdate={onUpdate} />
      ) : (
        <div className="text-sm text-gray-500 italic animate-pulse">
          Loading media...
        </div>
      )}
    </div>
  );
}


// --- Main App Component ---
function App() {
  const [messages, setMessages] = useState<Message[]>([])
  const [users, setUsers] = useState<User[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  
  const [selectedUser, setSelectedUser] = useState<string>("")
  const [startDate, setStartDate] = useState<string>("")
  const [endDate, setEndDate] = useState<string>("")
  
  const [currentPage, setCurrentPage] = useState(1);
  const [totalPages, setTotalPages] = useState(0);
  const [totalCount, setTotalCount] = useState(0);
  
  // --- UPDATED: These states are now for the JSON Export ---
  const [isSummarizing, setIsSummarizing] = useState(false); // Renamed to isExporting
  const [summary, setSummary] = useState<string | null>(null); // Renamed to exportResult
  const [summaryError, setSummaryError] = useState<string | null>(null); // Renamed to exportError

  // This is the stable useEffect that drives all data fetching
  useEffect(() => {
    const fetchAllData = async () => {
      setLoading(true);
      setError(null);

      try {
        // 1. Fetch Users (only on first load)
        if (users.length === 0) {
          const userResponse = await fetch(`${API_URL}/users`);
          if (!userResponse.ok) throw new Error('Failed to fetch users');
          const userData: User[] = await userResponse.json();
          setUsers(userData);
        }

        // 2. Fetch Messages (always)
        const params = new URLSearchParams();
        params.append("page", currentPage.toString());
        params.append("limit", MESSAGES_PER_PAGE.toString());
        
        if (selectedUser) params.append("telegram_user_id", selectedUser);
        if (startDate) params.append("start_date", new Date(startDate).toISOString());
        if (endDate) params.append("end_date", new Date(endDate).toISOString());
        
        const queryString = params.toString();
        const response = await fetch(`${API_URL}/messages?${queryString}`);
        
        if (!response.ok) {
          const err = await response.json();
          throw new Error(err.detail || `HTTP error! status: ${response.status}`);
        }
        
        const data: PaginatedMessages = await response.json();
        
        setMessages(data.messages);
        setTotalPages(data.total_pages);
        setCurrentPage(data.current_page);
        setTotalCount(data.total_count);

      } catch (e: any) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
    };
    
    fetchAllData();
    
  }, [currentPage, selectedUser, startDate, endDate, users.length]);


  const handleMediaUpdated = (updatedMedia: Media) => {
    setMessages(currentMessages => {
      return currentMessages.map(msg => {
        if (msg.id === updatedMedia.message_id) {
          return {
            ...msg,
            // Fallback to an empty array before mapping
            media: (msg.media || []).map(m => m.id === updatedMedia.id ? updatedMedia : m)
          };
        }
        return msg;
      });
    });
  };

  const handleFilterSubmit = () => {
    setCurrentPage(1); 
  }

  const handleClearFilters = () => {
    setSelectedUser("");
    setStartDate("");
    setEndDate("");
    setCurrentPage(1);
  }

  const handleNextPage = () => {
    if (currentPage < totalPages) {
      setCurrentPage(page => page + 1);
    }
  }

  const handlePrevPage = () => {
    if (currentPage > 1) {
      setCurrentPage(page => page - 1);
    }
  }

  // --- UPDATED: This function now EXPORTS JSON, not summarizes ---
  const handleSummarize = async () => {
    setIsSummarizing(true); // Re-using this state, means "isExporting"
    setError(null); 
    setSummaryError(null); 
    setSummary(null); 

    try {
      // --- Step 1: Fetch the structured JSON data ---
      const params = new URLSearchParams();
      if (selectedUser) params.append("telegram_user_id", selectedUser);
      if (startDate) params.append("start_date", new Date(startDate).toISOString());
      if (endDate) params.append("end_date", new Date(endDate).toISOString());
      
      const response = await fetch(`${API_URL}/messages/export?${params.toString()}`);
      
      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'Failed to fetch data for export');
      }
      
      // We expect an array of ExportMessage objects
      const exportData: ExportMessage[] = await response.json();

      if (!exportData || exportData.length === 0) {
        throw new Error("No text content found in the selected range to export.");
      }

      // --- Step 2: Convert the JSON object to a string for display ---
      // JSON.stringify(value, replacer, space) -- 2 spaces for nice indentation
      const jsonString = JSON.stringify(exportData, null, 2);
      
      // --- Step 3: Set the summary state to this new JSON string ---
      setSummary(jsonString);

      // --- Step 4: (REMOVED) No /summarize call ---

    } catch (e: any) {
      setSummaryError(e.message); 
    } finally {
      setIsSummarizing(false);
    }
  };
  
  // --- Smart handlers for date inputs (Unchanged) ---
const handleStartDateChange = (e: ChangeEvent<HTMLInputElement>) => {
    const newValue = e.target.value;
    if (!newValue) {
      setStartDate("");
      return;
    }
    const newDatePart = newValue.split('T')[0];
    // Safely check if startDate exists before splitting
    const oldDatePart = startDate ? startDate.split('T')[0] : "";
    
    if (newDatePart !== oldDatePart) {
      setStartDate(newDatePart + "T00:00");
    } else {
      setStartDate(newValue);
    }
  };

const handleEndDateChange = (e: ChangeEvent<HTMLInputElement>) => {
    const newValue = e.target.value;
    if (!newValue) {
      setEndDate("");
      return;
    }
    const newDatePart = newValue.split('T')[0];
    // Safely check if endDate exists before splitting
    const oldDatePart = endDate ? endDate.split('T')[0] : "";
    
    if (newDatePart !== oldDatePart) {
      setEndDate(newDatePart + "T23:59");
    } else {
      setEndDate(newValue);
    }
  };


  return (
    <div className="max-w-3xl mx-auto p-5 font-sans">
      <h1 className="text-3xl font-bold text-center text-white mb-8">
        Field Notes
      </h1>
      
      {/* Filter Bar (Unchanged) */}
      <div className="bg-gray-100 p-4 rounded-lg mb-6 shadow-sm text-black">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div>
            <label htmlFor="user-filter" className="block text-sm font-medium text-gray-700">User</label>
            <select
              id="user-filter"
              value={selectedUser}
              onChange={(e) => setSelectedUser(e.target.value)}
              className="mt-1 block w-full p-2 border border-gray-300 rounded-md shadow-sm"
            >
              <option value="">All Users</option>
              {users.map((user) => (
                <option key={user.id} value={user.telegram_user_id}>
                  {user.first_name} {user.last_name || ''}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="start-date" className="block text-sm font-medium text-gray-700">Start Date</label>
            <input
              type="datetime-local"
              id="start-date"
              value={startDate} 
              onChange={handleStartDateChange}
              className="mt-1 block w-full p-2 border border-gray-300 rounded-md shadow-sm"
            />
          </div>
          <div>
            <label htmlFor="end-date" className="block text-sm font-medium text-gray-700">End Date</label>
            <input
              type="datetime-local"
              id="end-date"
              value={endDate} 
              onChange={handleEndDateChange}
              className="mt-1 block w-full p-2 border border-gray-300 rounded-md shadow-sm"
            />
          </div>
        </div>
        <div className="flex gap-4 mt-4">
          <button
            onClick={handleFilterSubmit}
            className="w-full bg-blue-600 text-white p-2 rounded-md hover:bg-blue-700"
          >
            Apply Filters
          </button>
          <button
            onClick={handleClearFilters}
            className="w-full bg-gray-300 text-gray-800 p-2 rounded-md hover:bg-gray-400"
          >
            Clear Filters
          </button>
        </div>
      </div>

      {/* --- UPDATED: Summarize Button --- */}
     <div className='w-full flex justify-center my-4'>
       <button 
         onClick={handleSummarize}
         disabled={isSummarizing || loading} 
         className='bg-red-700 px-4 py-2 text-white rounded-xl cursor-pointer disabled:bg-gray-400'
       >
         {isSummarizing ? "Exporting..." : "Export Filtered JSON"}
       </button>
     </div>

     {/* --- UPDATED: Summary Display Box --- */}
     {isSummarizing && (
       <h2 className="text-xl font-bold text-center animate-pulse text-white">Exporting Data...</h2>
     )}
     {summaryError && (
        <div className="bg-red-100 border-red-400 border text-red-700 px-4 py-3 rounded-lg mb-6 shadow-sm" role="alert">
          <strong className="font-bold">Error: </strong>
          <span className="block sm:inline">{summaryError}</span>
        </div>
     )}
     {summary && (
       <div className="bg-blue-50 border-blue-200 border p-4 rounded-lg mb-6 shadow-sm text-black max-h-100 overflow-scroll">
         <div className='flex justify-between'>
           <h2 className="text-xl font-bold mb-2">Exported JSON</h2>
         <button onClick={()=>{setSummary(null)}} className="bg-red-600 text-white px-4 py-2 rounded-md hover:bg-red-700">
           X
         </button>
         </div>
         {/* Use a <pre> tag to correctly display formatted JSON */}
         <pre className="text-sm text-gray-800 my-2 whitespace-pre-wrap overflow-auto">
           {summary}
         </pre>
       </div>
     )}
      
      {/* Pagination Controls (Unchanged) */}
      <div className="flex justify-between items-center mb-4">
        <button
          onClick={handlePrevPage}
          disabled={currentPage <= 1 || loading}
          className="px-4 py-2 bg-gray-300 text-gray-800 rounded-md disabled:opacity-50 disabled:cursor-not-allowed"
        >
          ← Previous
        </button>
        <span className="text-gray-700">
          Page {currentPage} of {totalPages || 1} (Total: {totalCount} messages)
        </span>
        <button
          onClick={handleNextPage}
          disabled={currentPage >= totalPages || loading}
          className="px-4 py-2 bg-gray-300 text-gray-800 rounded-md disabled:opacity-50 disabled:cursor-not-allowed"
        >
          Next →
        </button>
      </div>

      {/* Message List (Unchanged) */}
      {loading && <h2 className="text-xl font-bold text-center animate-pulse">Loading...</h2>}
      {error && <h2 className="text-xl font-bold text-center text-red-600">Error: {error}</h2>}
      
      {!loading && !error && (
        <div className="flex flex-col gap-4">
          {messages.length === 0 && (
            <div className="text-center text-gray-500 text-lg">
              No messages found.
            </div>
          )}
          {messages.map((msg) => (
            <div key={msg.id} className="bg-white shadow-md rounded-lg p-4 border border-gray-200 text-black">
              <div className="flex justify-between items-center border-b border-gray-200 pb-2 mb-3">
                <strong className="text-lg font-semibold text-blue-600">
                  {msg.user?.first_name || 'Unknown User'}
                </strong>
                <span className="text-sm text-gray-500">
                  {new Date(msg.timestamp.endsWith('Z') ? msg.timestamp : msg.timestamp + 'Z').toLocaleString()}
                </span>
              </div>
              {msg.question && (
                <div className="bg-blue-50 border-l-4 border-blue-400 p-2 mb-2 rounded-r-md text-sm text-blue-800 italic">
                  <strong>Q: </strong> {msg.question}
                </div>
              )}
              {msg.text && (
                <p className="text-base text-gray-800 my-2 whitespace-pre-wrap">{msg.text}</p>
              )}
              {(msg.media || []).length > 0 && (
                <div className="mt-3 pt-3 border-t border-gray-200 border-dashed flex flex-col gap-4">
                  {msg.media.map((item) => (
                    <LazyMediaItem key={item.id} media={item} onUpdate={handleMediaUpdated} />
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      
      {/* Pagination Controls (Bottom) (Unchanged) */}
      {totalPages > 1 && (
        <div className="flex justify-between items-center mt-6 pt-4 border-t">
          <button
            onClick={handlePrevPage}
            disabled={currentPage <= 1 || loading}
            className="px-4 py-2 bg-gray-300 text-gray-800 rounded-md disabled:opacity-50 disabled:cursor-not-allowed"
          >
            ← Previous
          </button>
          <span className="text-gray-700">
            Page {currentPage} of {totalPages}
          </span>
          <button
            onClick={handleNextPage}
            disabled={currentPage >= totalPages || loading}
            className="px-4 py-2 bg-gray-300 text-gray-800 rounded-md disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Next →
          </button>
        </div>
      )}

    </div>
  )
}

export default App