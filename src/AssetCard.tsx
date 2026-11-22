import "./ProjectWorkspace.css";

// 素材记录类型（与后端 project_assets 表对应）。
export type Asset = {
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
  analysis_status?: string | null;
  analysis_error?: string | null;
};

// 秒数格式化为 m:ss。
function formatDuration(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  const remaining = Math.floor(seconds % 60);

  return `${minutes}:${String(remaining).padStart(2, "0")}`;
}

// 文件大小格式化为 MB。
function formatSize(bytes: number) {
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

// 分析状态的中文标签。
const STATUS_LABELS: Record<string, string> = {
  pending: "待分析",
  processing: "分析中",
  completed: "已分析",
  failed: "分析失败",
};

type AssetCardProps = {
  asset: Asset;
  projectId: string;
  apiBase: string;
  analyzing: boolean;
  onAnalyze: (assetId: string) => void;
};

export default function AssetCard({
  asset,
  projectId,
  apiBase,
  analyzing,
  onAnalyze,
}: AssetCardProps) {
  // 缩略图接口地址（带时间戳避免分析完成后浏览器缓存旧占位图）。
  const thumbnailUrl =
    `${apiBase}/api/projects/${projectId}/assets/` +
    `${asset.asset_id}/thumbnail`;

  const statusLabel =
    STATUS_LABELS[asset.analysis_status ?? "pending"] ??
    asset.analysis_status ??
    "待分析";

  return (
    <article className="asset-card">
      {asset.thumbnail_path ? (
        <img
          src={`${thumbnailUrl}?t=${asset.analysis_status}`}
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
        {asset.width ?? "?"} × {asset.height ?? "?"}
      </p>

      {asset.description ? (
        <p>{asset.description}</p>
      ) : (
        <p>等待内容分析</p>
      )}

      {asset.analysis_error && (
        <p className="error">{asset.analysis_error}</p>
      )}

      <div className="asset-card-footer">
        <span className={"status-" + (asset.analysis_status ?? "pending")}>
          {statusLabel}
        </span>

        <button
          disabled={
            analyzing ||
            asset.analysis_status === "processing"
          }
          onClick={() => onAnalyze(asset.asset_id)}
        >
          {asset.analysis_status === "completed"
            ? "重新分析"
            : "AI 分析"}
        </button>
      </div>
    </article>
  );
}
