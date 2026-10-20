
import { useEffect, useRef, useState } from "react";

type Chapter = {
  start: number;
  title: string;
  summary: string;
};

function App() {
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [error, setError] = useState("");
  const [uploadStatus, setUploadStatus] = useState("");
  const [videoId, setVideoId] = useState<string | null>(null);


  const videoRef = useRef<HTMLVideoElement>(null);

  // 释放本地视频临时 URL
  useEffect(() => {
    return () => {
      if (videoUrl) URL.revokeObjectURL(videoUrl);
    };
  }, [videoUrl]);

  
  async function handleUpload(file?: File) {
    if (!file) return;

    if (!file.name.toLowerCase().endsWith(".mp4")) {
      alert("目前仅支持 MP4 文件");
      return;
    }

    // 先更新本地播放器
    setVideoUrl(URL.createObjectURL(file));

    // 新视频不能沿用旧视频的章节
    setChapters([]);
    setVideoId(null);
    setUploadStatus("正在上传...");

    const formData = new FormData();
    formData.append("file", file);

    try {
      const response = await fetch(
        "http://127.0.0.1:8000/api/videos",
        {
          method: "POST",
          body: formData,
        }
      );

      if (!response.ok) {
        throw new Error(`上传失败：HTTP ${response.status}`);
      }

      const result = await response.json();

      setVideoId(result.video_id);
      setUploadStatus("上传成功");
    } catch (err) {
      setUploadStatus(
        err instanceof Error ? err.message : "上传失败"
      );
    }
  }


  function jumpToChapter(start: number) {
    const video = videoRef.current;

    if (!video) return;

    video.currentTime = start;
    void video.play().catch(() => {
      // 浏览器可能阻止自动播放，但仍可完成跳转
    });
  }

  function formatTime(seconds: number) {
    const min = Math.floor(seconds / 60);
    const sec = Math.floor(seconds % 60);

    return `${String(min).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  }

  return (
    <main style={{ maxWidth: 1100, margin: "40px auto", padding: 20 }}>
      <h1>SceneNote</h1>
      <p>快速了解长视频，找到值得看的片段。</p>

      <input
        type="file"
        accept="video/mp4"
        onChange={(e) => handleUpload(e.target.files?.[0])}
      />

      <div
        style={{
          display: "flex",
          gap: 24,
          marginTop: 30,
          alignItems: "flex-start",
        }}
      >
        <section style={{ flex: 2, minWidth: 0 }}>
          {videoUrl ? (
            <video
              key={videoUrl}
              ref={videoRef}
              src={videoUrl}
              controls
              style={{ width: "100%" }}
            />
          ) : (
            <p>请先选择一个视频文件</p>
          )}
          <p>{uploadStatus}</p>
          {videoId && <p>视频 ID：{videoId}</p>}
        </section>

        <section style={{ flex: 1, minWidth: 260 }}>
          <h2>AI 视频章节</h2>

          {error && <p style={{ color: "red" }}>{error}</p>}

          {!error && chapters.length === 0 && (
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
                cursor: videoUrl ? "pointer" : "not-allowed",
                border: "1px solid #ddd",
                borderRadius: 8,
                background: "#fff",
                color: "#222",
              }}
            >
              <strong>
                {formatTime(chapter.start)} · {chapter.title}
              </strong>
              <p style={{ marginBottom: 0 }}>{chapter.summary}</p>
            </button>
          ))}
        </section>
      </div>
    </main>
  );
}

export default App;
