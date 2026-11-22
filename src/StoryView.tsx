import { useEffect, useState } from "react";

import "./ProjectWorkspace.css";

// 故事主题类型（与后端 stories 表对应）。
type Story = {
  story_id: string;
  project_id: string;
  title: string;
  description: string;
  source: string;
  created_at: string;
  clip_count?: number;
};

// 故事中的片段（附带素材信息）。
type StoryClip = {
  clip_id: string;
  asset_id: string;
  start_time: number;
  end_time: number;
  description: string;
  relation_type: string;
  asset_filename: string;
  asset_recorded_at: string | null;
};

type StoryViewProps = {
  projectId: string;
  apiBase: string;
};

// 秒数格式化为 m:ss。
function formatTime(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  const remaining = Math.floor(seconds % 60);

  return `${minutes}:${String(remaining).padStart(2, "0")}`;
}

export default function StoryView({
  projectId,
  apiBase,
}: StoryViewProps) {
  const [stories, setStories] = useState<Story[]>([]);
  const [selected, setSelected] = useState<Story | null>(null);
  const [clips, setClips] = useState<StoryClip[]>([]);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  // 加载故事列表。
  async function loadStories() {
    const response = await fetch(
      `${apiBase}/api/projects/${projectId}/stories`
    );

    if (!response.ok) {
      throw new Error("故事列表加载失败");
    }

    const data = await response.json();

    setStories(data.stories);
  }

  // 加载指定故事的片段列表。
  async function loadStoryClips(story: Story) {
    const response = await fetch(
      `${apiBase}/api/projects/${projectId}/stories/${story.story_id}/clips`
    );

    if (!response.ok) {
      throw new Error("故事片段加载失败");
    }

    const data = await response.json();

    setClips(data.clips);
  }

  // 选中项目时加载故事列表。
  useEffect(() => {
    let cancelled = false;

    async function initialize() {
      try {
        const response = await fetch(
          `${apiBase}/api/projects/${projectId}/stories`
        );

        if (!response.ok) {
          throw new Error("故事列表加载失败");
        }

        const data = await response.json();

        if (!cancelled) {
          setStories(data.stories);
          setSelected(null);
          setClips([]);
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "故事列表加载失败"
          );
        }
      }
    }

    void initialize();

    return () => {
      cancelled = true;
    };
  }, [projectId, apiBase]);

  // 创建故事主题。
  async function createStory() {
    if (!title.trim()) {
      setError("请输入故事标题");
      return;
    }

    setLoading(true);
    setError("");
    setMessage("");

    try {
      const response = await fetch(
        `${apiBase}/api/projects/${projectId}/stories`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            title,
            description,
          }),
        }
      );

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || "故事创建失败");
      }

      await loadStories();
      setTitle("");
      setDescription("");
      setMessage("故事创建成功");
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "故事创建失败"
      );
    } finally {
      setLoading(false);
    }
  }

  // 删除故事，只清理关联，不影响素材。
  async function deleteStory(story: Story) {
    setLoading(true);
    setError("");
    setMessage("");

    try {
      const response = await fetch(
        `${apiBase}/api/projects/${projectId}/stories/${story.story_id}`,
        { method: "DELETE" }
      );

      if (!response.ok) {
        throw new Error("故事删除失败");
      }

      if (selected?.story_id === story.story_id) {
        setSelected(null);
        setClips([]);
      }

      await loadStories();
      setMessage("故事已删除，素材不受影响");
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "故事删除失败"
      );
    } finally {
      setLoading(false);
    }
  }

  // 选中故事并加载片段。
  async function selectStory(story: Story) {
    setSelected(story);
    setError("");
    setMessage("");

    try {
      await loadStoryClips(story);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "故事片段加载失败"
      );
    }
  }

  // 把片段从故事中移除。
  async function removeClip(clip: StoryClip) {
    if (!selected) return;

    setLoading(true);
    setError("");

    try {
      const response = await fetch(
        `${apiBase}/api/projects/${projectId}/stories/${selected.story_id}/clips/${clip.clip_id}`,
        { method: "DELETE" }
      );

      if (!response.ok) {
        throw new Error("片段移除失败");
      }

      await loadStoryClips(selected);
      await loadStories();
      setMessage("片段已从故事中移除");
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "片段移除失败"
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="workspace-panel">
      <h2>故事主题</h2>

      <p>
        按事件和创作意图组织素材，
        同一个片段可以属于多个故事。
      </p>

      <input
        value={title}
        maxLength={100}
        onChange={(e) => setTitle(e.target.value)}
        placeholder="故事标题，例如：山区风景"
      />

      <input
        value={description}
        maxLength={1000}
        onChange={(e) => setDescription(e.target.value)}
        placeholder="故事描述（可选）"
      />

      <button
        disabled={loading}
        onClick={() => void createStory()}
      >
        创建故事
      </button>

      {message && <p className="success">{message}</p>}
      {error && <p className="error">{error}</p>}

      {stories.length === 0 && <p>暂无故事主题</p>}

      {stories.map((story) => (
        <div key={story.story_id}>
          <button
            className={
              selected?.story_id === story.story_id
                ? "project-item active"
                : "project-item"
            }
            onClick={() => void selectStory(story)}
          >
            <strong>{story.title}</strong>
            <span>
              {story.clip_count ?? 0} 个片段
              {story.source === "ai" ? " · AI 建议" : ""}
            </span>
          </button>

          <button
            disabled={loading}
            onClick={() => void deleteStory(story)}
          >
            删除
          </button>
        </div>
      ))}

      {selected && (
        <div>
          <h3>{selected.title} 的片段</h3>

          {selected.description && (
            <p>{selected.description}</p>
          )}

          {clips.length === 0 && (
            <p>该故事还没有片段</p>
          )}

          {clips.map((clip) => (
            <div key={clip.clip_id} className="project-item">
              <div>
                <strong>{clip.asset_filename}</strong>
                <span>
                  {formatTime(clip.start_time)} -
                  {formatTime(clip.end_time)}
                  {clip.description ? ` · ${clip.description}` : ""}
                </span>
              </div>

              <button
                disabled={loading}
                onClick={() => void removeClip(clip)}
              >
                移除
              </button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
