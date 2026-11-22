import { useEffect, useState } from "react";

import "./ProjectWorkspace.css";

// 剪辑大纲条目（与后端 build_edit_outline 输出对应）。
type OutlineItem = {
  story_id: string;
  title: string;
  clip_count: number;
  candidate_minutes: number;
};

// 全部片段的整体统计。
type TotalStats = {
  clip_count: number;
  candidate_seconds: number;
  candidate_minutes: number;
};

// 单个风格的成片时长估算。
type StyleEstimate = {
  style: string;
  min_minutes: number;
  max_minutes: number;
};

// 大纲接口的完整响应。
type OutlineResponse = {
  project_id: string;
  outline: OutlineItem[];
  total: TotalStats;
  estimates: Record<string, StyleEstimate>;
};

// 剪辑规划记录。
type EditPlan = {
  plan_id: string;
  project_id: string;
  title: string;
  style: string;
  target_min_minutes: number | null;
  target_max_minutes: number | null;
  plan_json: unknown;
  created_at: string;
  updated_at: string;
};

// 风格的中文标签。
const STYLE_LABELS: Record<string, string> = {
  documentary: "记录型",
  concise: "精简型",
  ambient: "氛围型",
};

type EditPlanViewProps = {
  projectId: string;
  apiBase: string;
};

export default function EditPlanView({
  projectId,
  apiBase,
}: EditPlanViewProps) {
  const [outline, setOutline] = useState<OutlineItem[]>([]);
  const [total, setTotal] = useState<TotalStats | null>(null);
  const [estimates, setEstimates] = useState<
    Record<string, StyleEstimate>
  >({});
  const [plans, setPlans] = useState<EditPlan[]>([]);
  const [title, setTitle] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  // 加载剪辑大纲和已有规划。
  async function loadAll() {
    const [outlineRes, plansRes] = await Promise.all([
      fetch(
        `${apiBase}/api/projects/${projectId}/edit-plans/outline`
      ),
      fetch(`${apiBase}/api/projects/${projectId}/edit-plans`),
    ]);

    if (!outlineRes.ok || !plansRes.ok) {
      throw new Error("剪辑规划数据加载失败");
    }

    const outlineData: OutlineResponse =
      await outlineRes.json();

    const plansData = await plansRes.json();

    setOutline(outlineData.outline);
    setTotal(outlineData.total);
    setEstimates(outlineData.estimates);
    setPlans(plansData.plans);
  }

  // 选中项目时加载。
  useEffect(() => {
    let cancelled = false;

    async function initialize() {
      try {
        const data = await loadAll();

        if (!cancelled) void data;
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error
              ? err.message
              : "剪辑规划数据加载失败"
          );
        }
      }
    }

    void initialize();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  // 按当前大纲保存一份剪辑规划。
  async function savePlan(style: string) {
    if (!title.trim()) {
      setError("请输入规划标题");
      return;
    }

    const estimate = estimates[style];

    if (!estimate) {
      setError("请选择剪辑风格");
      return;
    }

    setLoading(true);
    setError("");
    setMessage("");

    try {
      const response = await fetch(
        `${apiBase}/api/projects/${projectId}/edit-plans`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            title: title.trim(),
            style,
            target_min_minutes: estimate.min_minutes,
            target_max_minutes: estimate.max_minutes,
            plan_json: {
              outline,
              total,
            },
          }),
        }
      );

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || "剪辑规划保存失败");
      }

      setTitle("");
      setMessage("剪辑规划已保存");

      await loadAll();
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "剪辑规划保存失败"
      );
    } finally {
      setLoading(false);
    }
  }

  // 删除规划。
  async function deletePlan(plan: EditPlan) {
    setLoading(true);
    setError("");
    setMessage("");

    try {
      const response = await fetch(
        `${apiBase}/api/projects/${projectId}/edit-plans/${plan.plan_id}`,
        { method: "DELETE" }
      );

      if (!response.ok) {
        throw new Error("剪辑规划删除失败");
      }

      await loadAll();
      setMessage("剪辑规划已删除");
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "剪辑规划删除失败"
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="workspace-panel">
      <h2>剪辑规划</h2>

      <p>
        从已确认的候选素材出发估算成片时长。
        比例只是初始参数，请以实际剪辑结果为准。
      </p>

      {total && (
        <p>
          候选素材共 {total.clip_count} 个片段、
          {total.candidate_minutes} 分钟（已去重）。
        </p>
      )}

      <h3>故事大纲</h3>

      {outline.length === 0 && (
        <p>还没有故事，请先在故事视图创建</p>
      )}

      {outline.map((item) => (
        <div key={item.story_id} className="project-item">
          <strong>{item.title}</strong>
          <span>
            {item.clip_count} 个片段 ·
            {item.candidate_minutes} 分钟
          </span>
        </div>
      ))}

      <h3>成片时长估算</h3>

      {Object.entries(estimates).map(([style, est]) => (
        <div key={style} className="project-item">
          <strong>
            {STYLE_LABELS[style] ?? style}
          </strong>
          <span>
            {est.min_minutes} - {est.max_minutes} 分钟
          </span>
        </div>
      ))}

      <h3>保存规划</h3>

      <input
        value={title}
        maxLength={100}
        onChange={(e) => setTitle(e.target.value)}
        placeholder="规划标题，例如：张家界 Vlog 初版"
      />

      {(["documentary", "concise", "ambient"] as const).map(
        (style) => (
          <button
            key={style}
            disabled={loading}
            onClick={() => void savePlan(style)}
          >
            保存为{STYLE_LABELS[style]}
          </button>
        )
      )}

      {message && <p className="success">{message}</p>}
      {error && <p className="error">{error}</p>}

      <h3>已保存的规划</h3>

      {plans.length === 0 && <p>暂无剪辑规划</p>}

      {plans.map((plan) => (
        <div key={plan.plan_id} className="project-item">
          <strong>{plan.title}</strong>
          <span>
            {STYLE_LABELS[plan.style] ?? plan.style} ·
            {plan.target_min_minutes} -
            {plan.target_max_minutes} 分钟
          </span>

          <button
            disabled={loading}
            onClick={() => void deletePlan(plan)}
          >
            删除
          </button>
        </div>
      ))}
    </section>
  );
}
