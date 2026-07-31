import { useApi } from "../api/context";
import { useQuery } from "../api/useQuery";
import { ErrorState, PageState } from "../components/PageState";
import { PageHeader } from "../components/PageHeader";
import { StatusBadge } from "../components/StatusBadge";

export function MessageChannelsPage() {
  const api = useApi();
  const query = useQuery(() => api.listMessageChannels(), [api]);

  return (
    <div className="page">
      <PageHeader eyebrow="SIGNED DELIVERY" title="消息通道" description="结构化确认与执行结果的签名投递健康；不是自然语言设备命令入口。" />
      {query.loading ? <PageState state="loading" label="正在读取消息通道状态" /> : null}
      {query.error ? <ErrorState error={query.error} onRetry={query.reload} /> : null}
      {query.data?.length === 0 ? <PageState state="empty" label="没有已注册消息通道" /> : null}
      {query.data?.map((channel) => (
        <section className="channel-workbench" key={channel.channel_id}>
          <div className="work-surface channel-summary">
            <div><p className="eyebrow">CHANNEL</p><h2 className="mono">{channel.channel_id}</h2></div>
            <StatusBadge value={channel.status} />
            <p>{channel.status === "configured" ? "签名出站与回调验证已配置。" : "未配置发送 URL；待确认动作仍会保留至过期。"}</p>
          </div>
          <div className="work-surface channel-contract">
            <div className="section-heading"><div><p className="eyebrow">CALLBACK CONTRACT</p><h2>签名回调</h2></div></div>
            <dl className="context-list">
              <div><dt>回调路径</dt><dd className="mono">{channel.callback_path}</dd></div>
              <div><dt>授权用户数</dt><dd>{channel.allowed_actor_count}</dd></div>
              <div><dt>签名</dt><dd className="mono">HMAC-SHA256</dd></div>
              <div><dt>防重放</dt><dd>timestamp + nonce</dd></div>
            </dl>
          </div>
          <div className="work-surface channel-flow">
            <p className="eyebrow">DELIVERY FLOW</p>
            <ol>
              <li><span>01</span><div><strong>生成确认</strong><small>自动高风险动作绑定 action_hash</small></div></li>
              <li><span>02</span><div><strong>签名投递</strong><small>只发送必要摘要与确认 ID</small></div></li>
              <li><span>03</span><div><strong>验证决定</strong><small>身份、签名、nonce 和原动作全部匹配</small></div></li>
            </ol>
          </div>
          <div className="channel-deliveries">
            <PageState
              state="empty"
              label="投递记录查询不可用"
              detail="当前后端仅暴露消息通道配置健康，不伪造投递历史。"
            />
          </div>
        </section>
      ))}
    </div>
  );
}
