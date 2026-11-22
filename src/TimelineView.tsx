import { useEffect, useState } from "react";

import AssetCard, { type Asset } from "./AssetCard";
import "./ProjectWorkspace.css";

// 时间线一天的分组数据（与后端 build_timeline 输出对应）。
type TimelineDay = {
  day_key: string;
  day_number: number | null;
  title: string;
  assets: Asset[];
};

type TimelineResponse = {
  project_id: string;
  total_days: number;
  days: TimelineDay[];
};

type TimelineViewProps = {
  projectId: string;
  apiBase: string;
};

export default function TimelineView({
  projectId,
  apiBase,
}: TimelineViewProps) {
  const [days, setDays] = useState<TimelineDay[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // 选中项目时加载时间线。
  useEffect(() => {
    let cancelled = false;

    async function initialize() {
      try {
        const response = await fetch(
          `${apiBase}/api/projects/${projectId}/timeline`
        );

        if (!response.ok) {
          throw new Error("时间线加载失败");
        }

        const data: TimelineResponse = await response.json();

        if (!cancelled) setDays(data.days);
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error
              ? err.message
              : "时间线加载失败"
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void initialize();

    return () => {
      cancelled = true;
    };
  }, [projectId, apiBase]);

  return (
    <section className="workspace-panel">
      <h2>旅行时间线</h2>

      <p>
        按拍摄日期自动分组。
        拍摄时间未知的素材归入单独分组。
      </p>

      {loading && <p>时间线加载中...</p>}
      {error && <p className="error">{error}</p>}

      {!loading && !error && days.length === 0 && (
        <p>暂无素材，请先扫描文件夹</p>
      )}

      {days.map((day) => (
        <div key={day.day_key}>
          <h3>
            {day.day_number !== null
              ? `Day ${day.day_number} · ${day.title}`
              : day.title}
          </h3>

          <p>${day.assets.length} 个素材</p>

          <div className="asset-grid">
            {day.assets.map((asset) => (
              <AssetCard
                key={asset.asset_id}
                asset={asset}
                projectId={projectId}
                apiBase={apiBase}
                analyzing={false}
                onAnalyze={() => {
                  // 时间线视图暂不提供分析入口，
                  // 分析操作在素材库视图中进行。
                }}
              />
            ))}
          </div>
        </div>
      ))}
    </section>
  );
}
