import { useEffect, useState } from "react";

function App() {
  const [videoUrl, setVideoUrl] = useState<string | null>(null);

  useEffect(() => {
    return () => {
      if (videoUrl) URL.revokeObjectURL(videoUrl);
    };
  }, [videoUrl]);

  function handleUpload(file?: File) {
    if (!file) return;

    if (file.type !== "video/mp4") {
      alert("第一版仅支持 MP4 视频");
      return;
    }

    setVideoUrl(URL.createObjectURL(file));
  }

  return (
    <main style={{ maxWidth: 800, margin: "40px auto" }}>
      <h1>SceneNote</h1>
      <p>快速了解长视频，找到值得看的片段。</p>

      <input
        type="file"
        accept="video/mp4"
        onChange={(e) => handleUpload(e.target.files?.[0])}
      />

      {videoUrl && (
        <video
          key={videoUrl}
          src={videoUrl}
          controls
          style={{ width: "100%", marginTop: 24 }}
        />
      )}
    </main>
  );
}

export default App;
