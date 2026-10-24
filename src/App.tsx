import { useEffect, useRef, useState } from "react";

const API_BASE = "http://127.0.0.1:8000";
const STORAGE_KEY = "scenenote_current_video_id";

type Chapter = {
  start: number;
  title: string;
  summary: string;
};

type VideoItem = {
  video_id: string;
  filename: string;
  size: number;
  uploaded_at: string;
  status: string;
  message: string;
  video_url: string;
};

type VideoListResponse = {
  total: number;
  videos: VideoItem[];
};

type VideoStatus = {
  video_id: string;
  status: string;
  message: string;
  chapters?: Chapter[];
};

type SearchMode = "keyword" | "semantic";

type SearchResult = {
  start: number;
  end: number;
  text: string;
  segment_indices?: number[];
  score?: number;
};

type SearchResponse = {
  video_id: string;
  query: string;
  mode: SearchMode;
  total: number;
  results: SearchResult[];
  cache_hit?: boolean | null;
  latency_ms?: number | null;
};

function formatTime(seconds: number) {
  const min = Math.floor(seconds / 60);
  const sec = Math.floor(seconds % 60);

  return `${String(min).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

function formatSize(bytes: number) {
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "未知时间"
    : date.toLocaleString();
}

function App() {
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [videoId, setVideoId] = useState<string | null>(null);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [error, setError] = useState("");
  const [uploadStatus, setUploadStatus] = useState("");
  const [videos, setVideos] = useState<VideoItem[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [uploading, setUploading] = useState(false);

  const [query, setQuery] = useState("");
  const [searchMode, setSearchMode] =
    useState<SearchMode>("keyword");
  const [searchResults, setSearchResults] =
    useState<SearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState("");
  const [searched, setSearched] = useState(false);
  const [searchInfo, setSearchInfo] = useState("");

  const videoRef = useRef<HTMLVideoElement>(null);
  const uploadGeneration = useRef(0);
  const searchGeneration = useRef(0);

  function resetSearch() {
    searchGeneration.current += 1;
    setQuery("");
    setSearchResults([]);
    setSearchError("");
    setSearchInfo("");
    setSearched(false);
    setSearching(false);
  }

  async function loadHistory(): Promise<VideoItem[]> {
    const response = await fetch(`${API_BASE}/api/videos`);

    if (!response.ok) {
      throw new Error(`历史记录加载失败：HTTP ${response.status}`);
    }

    const data: VideoListResponse = await response.json();
    setVideos(data.videos);
    return data.videos;
  }

  useEffect(() => {
    let cancelled = false;

    async function initialize() {
      try {
        const response = await fetch(`${API_BASE}/api/videos`);

        if (!response.ok) {
          throw new Error("无法连接历史视频接口");
        }

        const data: VideoListResponse = await response.json();

        if (cancelled) return;

        setVideos(data.videos);

        const savedId = localStorage.getItem(STORAGE_KEY);

        if (savedId) {
          const savedVideo = data.videos.find(
            (item) => item.video_id === savedId
          );

          if (savedVideo) {
            setVideoId(savedId);
            setVideoUrl(`${API_BASE}${savedVideo.video_url}`);
          } else {
            localStorage.removeItem(STORAGE_KEY);
          }
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error
              ? err.message
              : "历史记录加载失败"
          );
        }
      } finally {
        if (!cancelled) setLoadingHistory(false);
      }
    }

    void initialize();

    return () => {
      cancelled = true;
    };
  }, []);

  function selectVideo(item: VideoItem) {
    uploadGeneration.current += 1;
    resetSearch();

    setVideoId(item.video_id);
    setVideoUrl(`${API_BASE}${item.video_url}`);
    setChapters([]);
    setError("");
    setUploadStatus("正在读取历史视频...");

    localStorage.setItem(STORAGE_KEY, item.video_id);
  }

  async function handleUpload(file?: File) {
    if (!file) return;

    if (!file.name.toLowerCase().endsWith(".mp4")) {
      setError("目前仅支持 MP4 文件");
      return;
    }

    if (file.size > 200 * 1024 * 1024) {
      setError("视频不能超过 200 MB");
      return;
    }

    const generation = ++uploadGeneration.current;

    resetSearch();
    setUploading(true);
    setError("");
    setChapters([]);
    setVideoId(null);
    setVideoUrl(null);
    setUploadStatus("正在上传...");

    localStorage.removeItem(STORAGE_KEY);

    const formData = new FormData();
    formData.append("file", file);

    try {
      const response = await fetch(`${API_BASE}/api/videos`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        const detail = await response.json().catch(() => null);
        throw new Error(
          detail?.detail || `上传失败：HTTP ${response.status}`
        );
      }

      const result = await response.json();

      if (generation !== uploadGeneration.current) return;

      setVideoId(result.video_id);
      setVideoUrl(
        `${API_BASE}/api/videos/${result.video_id}/file`
      );
      setUploadStatus("上传成功，等待分析");

      localStorage.setItem(STORAGE_KEY, result.video_id);

      try {
        await loadHistory();
      } catch {
        setError("上传成功，但历史列表暂时刷新失败");
      }
    } catch (err) {
      if (generation !== uploadGeneration.current) return;

      setError(
        err instanceof Error ? err.message : "上传失败"
      );
      setUploadStatus("");
    } finally {
      if (generation === uploadGeneration.current) {
        setUploading(false);
      }
    }
  }

  useEffect(() => {
    if (!videoId) return;

    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function pollStatus() {
      try {
        const response = await fetch(
          `${API_BASE}/api/videos/${videoId}`
        );

        if (!response.ok) {
          throw new Error(
            `状态查询失败：HTTP ${response.status}`
          );
        }

        const data: VideoStatus = await response.json();

        if (stopped) return;

        setUploadStatus(data.message);
        setError("");

        if (data.status === "completed") {
          setChapters(data.chapters ?? []);
          return;
        }

        if (data.status === "failed") {
          setError(data.message || "视频分析失败");
          return;
        }

        if (data.status === "unknown") {
          setError("无法确定该视频的分析状态");
          return;
        }

        timer = setTimeout(pollStatus, 2000);
      } catch (err) {
        if (stopped) return;

        setError(
          err instanceof Error
            ? err.message
            : "状态查询失败"
        );

        timer = setTimeout(pollStatus, 5000);
      }
    }

    void pollStatus();

    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [videoId]);

  function jumpToTime(start: number) {
    const video = videoRef.current;
    if (!video) return;

    video.currentTime = start;
    void video.play().catch(() => {});
  }

  async function handleSearch() {
    const keyword = query.trim();

    if (!videoId || !keyword || searching) return;

    const generation = ++searchGeneration.current;

    setSearching(true);
    setSearchError("");
    setSearchResults([]);
    setSearchInfo("");
    setSearched(false);

    try {
      const params = new URLSearchParams({
        q: keyword,
        mode: searchMode,
        top_k: "5",
      });

      const response = await fetch(
        `${API_BASE}/api/videos/${videoId}/search?${params}`
      );

      if (!response.ok) {
        const detail = await response.json().catch(() => null);
        throw new Error(
          detail?.detail || `搜索失败：HTTP ${response.status}`
        );
      }

      const data: SearchResponse = await response.json();

      if (generation !== searchGeneration.current) return;

      setSearchResults(data.results);
      setSearched(true);

      if (data.mode === "semantic") {
        setSearchInfo(
          `向量缓存：${data.cache_hit ? "命中" : "首次构建"}；` +
          `耗时：${data.latency_ms ?? "未知"} ms`
        );
      } else {
        setSearchInfo(`找到 ${data.total} 条关键词结果`);
      }
    } catch (err) {
      if (generation !== searchGeneration.current) return;

      setSearchError(
        err instanceof Error ? err.message : "搜索失败"
      );
    } finally {
      if (generation === searchGeneration.current) {
        setSearching(false);
      }
    }
  }

  return (
    <main
      style={{
        maxWidth: 1200,
        margin: "40px auto",
        padding: 20,
      }}
    >
      <h1>SceneNote</h1>
      <p>快速理解长视频，找到值得看的片段。</p>

      <div
        style={{
          display: "grid",
          gridTemplateColumns:
            "minmax(220px, 1fr) minmax(0, 2fr)",
          gap: 24,
          marginTop: 24,
        }}
      >
        <aside>
          <h2>历史视频</h2>

          {loadingHistory && <p>正在加载历史记录...</p>}

          {!loadingHistory && videos.length === 0 && (
            <p>暂无历史视频</p>
          )}

          <button
            onClick={() => {
              void loadHistory().catch((err) => {
                setError(
                  err instanceof Error ? err.message : "刷新失败"
                );
              });
            }}
          >
            刷新列表
          </button>

          <div style={{ marginTop: 12 }}>
            {videos.map((item) => (
              <button
                key={item.video_id}
                onClick={() => selectVideo(item)}
                style={{
                  display: "block",
                  width: "100%",
                  textAlign: "left",
                  padding: 12,
                  marginBottom: 10,
                  borderRadius: 8,
                  border:
                    item.video_id === videoId
                      ? "2px solid #2563eb"
                      : "1px solid #ddd",
                  background:
                    item.video_id === videoId
                      ? "#eff6ff"
                      : "#fff",
                  color: "#222",
                  cursor: "pointer",
                  overflowWrap: "anywhere",
                }}
              >
                <strong>{item.filename}</strong>
                <div style={{ fontSize: 12, marginTop: 6 }}>
                  {formatSize(item.size)}
                </div>
                <div style={{ fontSize: 12 }}>
                  {formatDate(item.uploaded_at)}
                </div>
                <div style={{ fontSize: 12 }}>
                  状态：{item.status}
                </div>
              </button>
            ))}
          </div>
        </aside>

        <div>
          <h2>上传新视频</h2>

          <input
            type="file"
            accept="video/mp4"
            disabled={uploading}
            onChange={(e) => {
              const file = e.target.files?.[0];
              void handleUpload(file);
              e.target.value = "";
            }}
          />

          <div style={{ marginTop: 24 }}>
            {videoUrl ? (
              <video
                key={videoUrl}
                ref={videoRef}
                src={videoUrl}
                controls
                style={{ width: "100%" }}
              />
            ) : (
              <p>请选择历史视频，或上传新视频。</p>
            )}
          </div>

          <p>{uploadStatus}</p>

          {videoId && (
            <p style={{ fontSize: 12 }}>
              视频 ID：{videoId}
            </p>
          )}

          {error && (
            <p style={{ color: "#dc2626" }}>{error}</p>
          )}

          <h2>AI 视频章节</h2>

          {chapters.length === 0 && <p>暂无章节</p>}

          {chapters.map((chapter, index) => (
            <button
              key={index}
              disabled={!videoUrl}
              onClick={() => jumpToTime(chapter.start)}
              style={{
                display: "block",
                width: "100%",
                textAlign: "left",
                padding: 14,
                marginBottom: 12,
                cursor: "pointer",
                border: "1px solid #ddd",
                borderRadius: 8,
                background: "#fff",
                color: "#222",
              }}
            >
              <strong>
                {formatTime(chapter.start)} · {chapter.title}
              </strong>
              <p style={{ marginBottom: 0 }}>
                {chapter.summary}
              </p>
            </button>
          ))}

          <hr style={{ margin: "28px 0" }} />

          <h2>视频片段搜索</h2>

          <div style={{ display: "flex", gap: 12, marginBottom: 12 }}>
            <label>
              <input
                type="radio"
                name="searchMode"
                checked={searchMode === "keyword"}
                onChange={() => {
                  resetSearch();
                  setSearchMode("keyword");
                }}
              />
              关键词搜索
            </label>

            <label>
              <input
                type="radio"
                name="searchMode"
                checked={searchMode === "semantic"}
                onChange={() => {
                  resetSearch();
                  setSearchMode("semantic");
                }}
              />
              语义搜索（实验）
            </label>
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              void handleSearch();
            }}
            style={{ display: "flex", gap: 8 }}
          >
            <input
              value={query}
              maxLength={100}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={
                searchMode === "keyword"
                  ? "输入视频中出现的关键词"
                  : "描述你想找到的视频内容"
              }
              style={{
                flex: 1,
                padding: 10,
                minWidth: 0,
              }}
            />

            <button
              type="submit"
              disabled={!videoId || !query.trim() || searching}
            >
              {searching ? "搜索中..." : "搜索"}
            </button>
          </form>

          {searchMode === "semantic" && (
            <p style={{ fontSize: 12, color: "#666" }}>
              实验模式：目前未设置相关性阈值，
              结果可能包含无关片段。
            </p>
          )}

          {searchError && (
            <p style={{ color: "#dc2626" }}>{searchError}</p>
          )}

          {searchInfo && <p>{searchInfo}</p>}

          {searched && searchResults.length === 0 && (
            <p>没有找到匹配结果。</p>
          )}

          <div style={{ marginTop: 16 }}>
            {searchResults.map((result, index) => (
              <button
                key={`${result.start}-${index}`}
                onClick={() => jumpToTime(result.start)}
                style={{
                  display: "block",
                  width: "100%",
                  textAlign: "left",
                  padding: 14,
                  marginBottom: 10,
                  border: "1px solid #ddd",
                  borderRadius: 8,
                  background: "#fff",
                  color: "#222",
                  cursor: "pointer",
                }}
              >
                <strong>
                  {formatTime(result.start)}–
                  {formatTime(result.end)}
                </strong>

                {result.score !== undefined && (
                  <span style={{ marginLeft: 12, color: "#666" }}>
                    相似度：{result.score.toFixed(4)}
                  </span>
                )}

                <p style={{ marginBottom: 0 }}>
                  {result.text}
                </p>
              </button>
            ))}
          </div>
        </div>
      </div>
    </main>
  );
}

export default App;
