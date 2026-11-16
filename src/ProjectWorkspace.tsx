import { useEffect, useState } from "react";

import "./ProjectWorkspace.css";

// 后端 API 地址，与 App.tsx 保持一致。
const API_BASE = "http://127.0.0.1:8000";

// 旅行项目类型定义。
type Project = {
  project_id: string;
  name: string;
  description: string;
  asset_count?: number;
};

// 素材记录类型定义（对应 project_assets 表）。
type Asset = {
  asset_id: string;
  project_id: string;
  filename: string;
  source_path: string;
  file_size: number;
  file_mtime: number;
  recorded_at: string | null;
  duration: number;
  width: number | null;
  height: number | null;
  thumbnail_path?: string | null;
  scene?: string | null;
  description?: string | null;
};

type ProjectListResponse = {
  total: number;
  projects: Project[];
};

type AssetListResponse = {
  total: number;
  assets: Asset[];
};

type ScanResponse = {
  project_id: string;
  total: number;
  saved: number;
  updated: number;
  skipped: number;
  errors: {
    filename: string;
    source_path: string;
    error: string;
  }[];
};

// 秒数格式化为 m:ss。
function formatDuration(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  const remaining = Math.floor(seconds % 60);

  return `${minutes}:${String(remaining).padStart(2, "0")}`;
}

// 字节数格式化为 KB / MB。
function formatSize(bytes: number) {
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }

  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

// 拍摄时间格式化为本地日期，缺失或无效时返回未知。
function formatDate(value: string | null) {
  if (!value) return "拍摄时间未知";

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return "拍摄时间未知";
  }

  return date.toLocaleDateString();
}

export default function ProjectWorkspace() {
  // 项目与素材数据。
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [assets, setAssets] = useState<Asset[]>([]);

  // 表单输入。
  const [projectName, setProjectName] = useState("");
  const [directory, setDirectory] = useState("");
  const [description, setDescription] = useState("");
  const [query, setQuery] = useState("");

  // 界面状态。
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  // 加载项目列表。
  async function loadProjects() {
    const response = await fetch(`${API_BASE}/api/projects`);

    if (!response.ok) {
      throw new Error("项目列表加载失败");
    }

    const data: ProjectListResponse = await response.json();

    setProjects(data.projects);

    return data.projects;
  }

  // 加载指定项目的素材列表。
  async function loadAssets(id: string) {
    const response = await fetch(
      `${API_BASE}/api/projects/${id}/assets`
    );

    if (!response.ok) {
      throw new Error("素材列表加载失败");
    }

    const data: AssetListResponse = await response.json();

    setAssets(data.assets);
  }

  // 首次进入时加载项目列表。
  useEffect(() => {
    let cancelled = false;

    async function initialize() {
      try {
        const response = await fetch(`${API_BASE}/api/projects`);

        if (!response.ok) {
          throw new Error("项目列表加载失败");
        }

        const data: ProjectListResponse = await response.json();

        if (!cancelled) setProjects(data.projects);
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "项目列表加载失败"
          );
        }
      }
    }

    void initialize();

    return () => {
      cancelled = true;
    };
  }, []);

  // 创建旅行项目。
  async function createProject() {
    if (!projectName.trim()) {
      setError("请输入旅行项目名称");
      return;
    }

    setLoading(true);
    setError("");
    setMessage("");

    try {
      const response = await fetch(`${API_BASE}/api/projects`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: projectName,
          description,
        }),
      });

      if (!response.ok) {
        const detail = await response
          .json()
          .catch(() => null);

        throw new Error(detail?.detail || "项目创建失败");
      }

      const project: Project = await response.json();

      await loadProjects();

      setProjectId(project.project_id);
      setProjectName("");
      setDescription("");
      setAssets([]);

      setMessage("旅行项目创建成功");
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "项目创建失败"
      );
    } finally {
      setLoading(false);
    }
  }

  // 扫描本地素材目录。
  async function scanDirectory() {
    if (!projectId) {
      setError("请先选择旅行项目");
      return;
    }

    if (!directory.trim()) {
      setError("请输入素材文件夹路径");
      return;
    }

    setLoading(true);
    setError("");
    setMessage("正在扫描素材...");

    try {
      const response = await fetch(
        `${API_BASE}/api/projects/${projectId}/scan`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ directory }),
        }
      );

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || "素材扫描失败");
      }

      const result = data as ScanResponse;

      await loadAssets(projectId);
      await loadProjects();

      setMessage(
        `扫描完成：发现 ${result.total} 个素材，` +
        `保存 ${result.saved} 个，` +
        `失败 ${result.errors.length} 个`
      );
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "扫描失败"
      );
    } finally {
      setLoading(false);
    }
  }

  // 选中某个项目并加载素材。
  async function selectProject(id: string) {
    setProjectId(id);
    setAssets([]);
    setError("");
    setMessage("");

    try {
      await loadAssets(id);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "素材加载失败"
      );
    }
  }

  // 按文件名和已生成的内容描述做前端过滤。
  const filteredAssets = assets.filter((asset) => {
    const text = [
      asset.filename,
      asset.scene || "",
      asset.description || "",
    ]
      .join(" ")
      .toLowerCase();

    return text.includes(query.toLowerCase());
  });

  // 按拍摄日期分组展示。
  const groups = filteredAssets.reduce(
    (result, asset) => {
      const day = asset.recorded_at
        ? formatDate(asset.recorded_at)
        : "拍摄时间未知";

      if (!result[day]) {
        result[day] = [];
      }

      result[day].push(asset);

      return result;
    },
    {} as Record<string, Asset[]>
  );

  return (
    <main className="workspace">
      <header>
        <h1>SceneNote</h1>
        <p>让旅行素材更容易整理和重新发现。</p>
      </header>

      <section className="workspace-panel">
        <h2>创建旅行项目</h2>

        <input
          value={projectName}
          maxLength={100}
          onChange={(e) => setProjectName(e.target.value)}
          placeholder="例如：张家界毕业旅行"
        />

        <input
          value={description}
          maxLength={1000}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="旅行描述（可选）"
        />

        <button
          disabled={loading}
          onClick={() => void createProject()}
        >
          创建项目
        </button>
      </section>

      <section className="workspace-panel">
        <h2>我的旅行项目</h2>

        {projects.length === 0 && <p>暂无旅行项目</p>}

        {projects.map((project) => (
          <button
            key={project.project_id}
            className={
              project.project_id === projectId
                ? "project-item active"
                : "project-item"
            }
            onClick={() =>
              void selectProject(project.project_id)
            }
          >
            <strong>{project.name}</strong>
            <span>
              {project.asset_count ?? 0} 个素材
            </span>
          </button>
        ))}
      </section>

      <section className="workspace-panel">
        <h2>导入旅行素材</h2>

        <p>
          输入 Windows 本地素材文件夹路径。
          支持已同步到电脑的网盘文件夹。
          目录必须位于后端允许的素材根目录内。
        </p>

        <input
          value={directory}
          maxLength={1000}
          onChange={(e) => setDirectory(e.target.value)}
          placeholder="D:\Travel\Zhangjiajie"
        />

        <button
          disabled={loading || !projectId}
          onClick={() => void scanDirectory()}
        >
          {loading ? "处理中..." : "扫描文件夹"}
        </button>
      </section>

      {message && <p className="success">{message}</p>}
      {error && <p className="error">{error}</p>}

      {projectId && (
        <section className="workspace-panel">
          <h2>旅行素材库</h2>

          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索文件名或已生成的内容描述"
          />

          <p>共 {filteredAssets.length} 个素材</p>

          {Object.entries(groups).map(([day, dayAssets]) => (
            <div key={day}>
              <h3>{day}</h3>

              <div className="asset-grid">
                {dayAssets.map((asset) => (
                  <article
                    className="asset-card"
                    key={asset.asset_id}
                  >
                    {asset.thumbnail_path ? (
                      <img
                        src={
                          `${API_BASE}/api/projects/` +
                          `${projectId}/assets/` +
                          `${asset.asset_id}/thumbnail`
                        }
                        alt={asset.filename}
                      />
                    ) : (
                      <div className="thumbnail-placeholder">
                        暂无缩略图
                      </div>
                    )}

                    <strong>{asset.filename}</strong>

                    <p>
                      {formatDuration(asset.duration)}
                      {" · "}
                      {formatSize(asset.file_size)}
                    </p>

                    <p>
                      {asset.width ?? "?"} ×{" "}
                      {asset.height ?? "?"}
                    </p>

                    <p>
                      {asset.description || "等待内容分析"}
                    </p>
                  </article>
                ))}
              </div>
            </div>
          ))}
        </section>
      )}
    </main>
  );
}
