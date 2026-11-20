/** 设置页共享的脱敏接口类型；厂商密钥不属于任何响应类型。 */
export interface Connection { id: string; name: string; provider: string; protocol: string; base_url: string; enabled: boolean; key_configured: boolean; endpoint_version: number; read_only?: boolean }
export interface Profile { id: string; connection_id: string; name: string; model: string; kind: string; enabled: boolean; revision_id: string; endpoint_version: number; timeout_seconds: number; max_output_tokens: number; temperature: number | null; thinking: string; api_version: string; probes: { kind: string; succeeded: boolean; detail: string; created_at: string }[] }
export interface Space { id: string; model_name: string; model: string; revision_id: string; status: string; indexed: number; total: number; error: string | null; activate_when_ready: boolean }
export interface Preset { id: string; name: string; protocol: string; base_url: string }
export interface Overview { connections: Connection[]; models: Profile[]; bindings: Record<string, string | null>; active_space_id: string | null; spaces: Space[]; presets: Preset[]; credential_ready: boolean; defaults: { timeout_seconds: number; max_output_tokens: number } }
