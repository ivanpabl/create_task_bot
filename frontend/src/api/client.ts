const API_BASE = import.meta.env.VITE_API_URL || '';
const TOKEN_KEY = 'jwt_token';

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

export function isAuthenticated(): boolean {
  return !!getToken();
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function raw(path: string, options: RequestInit = {}): Promise<Response> {
  const token = getToken();
  const headers: Record<string, string> = {
    ...((options.headers as Record<string, string>) || {}),
  };
  if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (!res.ok) {
    if (res.status === 401) clearToken();
    const body = await res.json().catch(() => null);
    const detail = typeof body?.detail === 'string' ? body.detail : res.statusText;
    throw new ApiError(res.status, detail || `HTTP ${res.status}`);
  }
  return res;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await raw(path, options);
  return res.json();
}

export interface User {
  id: string;
  discord_id: string;
  username: string;
}

export type TaskStatus = 'open' | 'in_progress' | 'done';
export type Urgency = 'high' | 'medium' | 'low';

export interface Task {
  id: string;
  user_id: string | null;
  source: string | null;
  content: string;
  context: string | null;
  author: string | null;
  assignee: string | null;
  deadline: string | null;
  urgency: Urgency | string;
  status: TaskStatus | string;
  channel: string | null;
  guild_id: string | null;
  keywords: string[] | null;
}

export interface AuthResponse {
  token: string;
  user: User;
}

export interface PairingCode {
  code: string;
  expires_in: number;
}

export function getAuthUrl(): string {
  return `${API_BASE}/auth/discord`;
}

export function exchangeToken(): Promise<AuthResponse> {
  return request<AuthResponse>('/auth/callback');
}

export function getMe(): Promise<User> {
  return request<User>('/auth/me');
}

export function getTasks(params?: {
  status?: string;
  urgency?: string;
  source?: string;
}): Promise<Task[]> {
  const searchParams = new URLSearchParams();
  if (params?.status) searchParams.set('status', params.status);
  if (params?.urgency) searchParams.set('urgency', params.urgency);
  if (params?.source) searchParams.set('source', params.source);
  const qs = searchParams.toString();
  return request<Task[]>(`/api/tasks${qs ? `?${qs}` : ''}`);
}

export function updateTask(id: string, data: { status?: string; assignee?: string }): Promise<Task> {
  return request<Task>(`/api/tasks/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export function getPairingCode(): Promise<PairingCode> {
  return request<PairingCode>('/api/pairing/code');
}

export async function downloadAgent(filename = 'discord-task-agent.zip'): Promise<void> {
  const res = await raw('/api/agent/download');
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
