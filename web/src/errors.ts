const TEXTS: Record<string, string> = {
  not_allowed: 'Этот адрес не в списке разрешённых.',
  account_disabled: 'Учётная запись отключена.',
  bad_state: 'Сессия входа не совпала, попробуйте ещё раз.',
  oauth_error: 'Яндекс отклонил вход.',
  oauth_upstream: 'Яндекс недоступен, попробуйте позже.',
}

export function loginErrorText(code: string | null): string {
  if (!code) return ''
  return TEXTS[code] ?? `Не удалось войти (${code}).`
}
