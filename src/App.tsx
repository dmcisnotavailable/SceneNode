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

  const videoRef = useRef<HTMLVideoElement>(null);
  const uploadGeneration = useRef(0);

  // 1. 查询历史视频列表
  async function loadHistory(): Promise<VideoItem[]> {
    const response = await fetch(`${API_BASE}/api/videos`);

    if (!response.ok) {
      throw new Error(`历史记录加载失败：HTTP ${response.status}`);
    }

    const data: VideoListResponse = await response.json();
    setVideos(data.videos);

    return data.videos;
  }

  // 2. 页面首次打开时，恢复上一次选中的视频
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
        if (!cancelled) {
          setLoadingHistory(false);
        }
      }
    }

    void initialize();

    return () => {
      cancelled = true;
    };
  }, []);

  // 3. 切换到历史视频
  function selectVideo(item: VideoItem) {
    uploadGeneration.current += 1;

    setVideoId(item.video_id);
    setVideoUrl(`${API_BASE}${item.video_url}`);
    setChapters([]);
    setError("");
    setUploadStatus("正在读取历史视频...");

    localStorage.setItem(STORAGE_KEY, item.video_id);
  }

  // 4. 上传新视频
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

      // 防止较早的上传结果覆盖用户新选中的视频
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

  // 5. 根据当前 videoId 查询分析状态
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

        // 其他状态继续轮询
        timer = setTimeout(pollStatus, 2000);
      } catch (err) {
        if (stopped) return;

        setError(
          err instanceof Error
            ? err.message
            : "状态查询失败"
        );

        // 网络异常时继续尝试，不直接丢失任务
        timer = setTimeout(pollStatus, 5000);
      }
    }

    void pollStatus();

    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [videoId]);

  // 6. 点击章节跳转
  function jumpToChapter(start: number) {
    const video = videoRef.current;
    if (!video) return;

    video.currentTime = start;
    void video.play().catch(() => {});
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
        {/* 左侧：历史视频 */}
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
                  err instanceof Error
                    ? err.message
                    : "刷新失败"
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

        {/* 右侧：播放器与章节 */}
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

          {chapters.length === 0 && (
            <p>暂无章节</p>
          )}

          {chapters.map((chapter, index) => (
            <button
              key={index}
              disabled={!videoUrl}
              onClick={() => jumpToChapter(chapter.start)}
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
        </div>
      </div>
    </main>
  );
}

export default App;
