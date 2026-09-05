export async function api<T>(url: string, options: RequestInit = {}): Promise<T> {
  const resp = await fetch(url, options)
  if (!resp.ok) {
    const text = await resp.text()
    throw new Error(text || resp.statusText)
  }
  return (await resp.json()) as T
}

export function postJson<T>(url: string, body: unknown): Promise<T> {
  return api<T>(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  })
}
