const API_BASE = import.meta.env.VITE_API_URL || '';

function getToken(): string | null {
  return localStorage.getItem('jwt_token');
}

export function setToken(token: string) {
  localStorage.setItem('jwt_token', token);
}

export function clearToken() {
  localStorage.removeItem('jwt_token');
}

export function isAuthenticated(): boolean {
  return !!getToken();
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((options.headers as Record<string, string>) || {}),
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (!res.ok) {
    if (res.status === 401) clearToken();
    throw new Error(`HTTP ${res.status}: ${res.statusText}`);
  }
  return res.json();
}

export interface User {
  id: string;
  discord_id: string;
  username: string;
}

export interface Task {
  id: string;
  user_id: string | null;
  source: string | null;
  content: string;
  context: string | null;
  author: string | null;
  assignee: string | null;
  deadline: string | null;
  urgency: string;
  status: string;
  channel: string | null;
  guild_id: string | null;
  keywords: string[] | null;
}

export interface AuthResponse {
  token: string;
  user: User;
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
