(() => {
  'use strict';
  const chat = document.getElementById('chat');
  if (!chat) return;
  const list = document.getElementById('chat-messages');
  const notice = document.getElementById('chat-update');
  const status = document.getElementById('chat-state');
  let cursor = chat.dataset.cursor, stopped = false, timer, delay = 10000;
  let permissions = {can_moderate: chat.dataset.moderate === 'true', can_original: chat.dataset.original === 'true'};
  const base = chat.dataset.changesUrl.replace(/changes\/$/, '');
  const denied = response => [401, 403, 404].includes(response.status);
  const loseAccess = () => {
    stopped = true;
    clearTimeout(timer);
    chat.replaceChildren();
    const heading = document.createElement('h1');
    heading.textContent = 'Доступ к чату прекращен';
    chat.append(heading);
  };
  const applyState = state => {
    permissions = state;
    status.textContent = state.archived ? 'Архив — только чтение' : state.time_locked ? 'Чат доступен только для чтения' : state.closed ? 'Чат закрыт — только чтение' : 'Чат открыт';
    document.getElementById('chat-compose').hidden = !state.can_send;
    const close = document.getElementById('chat-close');
    if (close) {
      close.hidden = !state.can_close;
      close.querySelector('[name=version]').value = state.version;
    }
  };
  const applyMessage = message => {
    document.getElementById('chat-empty')?.remove();
    let row = document.getElementById(`message-${message.id}`);
    if (row && BigInt(row.dataset.changeId) > BigInt(message.change_id)) return;
    if (!row) {
      row = document.createElement('li');
      row.id = `message-${message.id}`;
      row.dataset.messageId = message.id;
      for (const name of ['author', 'date', 'text']) {
        const p = document.createElement('p'); p.className = `message-${name}`; row.append(p);
      }
      const actions = document.createElement('div'); actions.className = 'message-actions'; row.append(actions);
      const following = [...list.children].find(el => BigInt(el.dataset.messageId) > BigInt(message.id));
      list.insertBefore(row, following || null);
    }
    row.dataset.changeId = message.change_id;
    row.querySelector('.message-author').textContent = message.author + (message.groups.length ? ` · ${message.groups.join(', ')}` : '');
    row.querySelector('.message-date').textContent = `${message.created_at} (Москва)`;
    row.querySelector('.message-text').textContent = message.hidden ? `Сообщение скрыто. Причина: ${message.reason}` : message.text;
    const actions = row.querySelector('.message-actions');
    // Keep a moderator's draft reason intact while permissions/state are unchanged.
    const mode = message.hidden ? (permissions.can_original ? 'original' : '') : (permissions.can_moderate ? 'moderate' : '');
    if (actions.dataset.mode === mode) return;
    actions.dataset.mode = mode;
    actions.replaceChildren();
    if (mode === 'original') {
      const link = document.createElement('a');
      link.href = `${base}messages/${message.id}/original/`; link.textContent = `Оригинал сообщения ${message.id}`; actions.append(link);
    } else if (mode === 'moderate') {
      const details = document.getElementById('chat-moderation').content.cloneNode(true);
      details.querySelector('summary').textContent = `Скрыть сообщение ${message.id}`;
      details.querySelector('form').action = `${base}messages/${message.id}/hide/`;
      details.querySelector('input[name=reason]').id = `reason-${message.id}`;
      details.querySelector('label').htmlFor = `reason-${message.id}`;
      actions.append(details);
    }
  };
  // Mark server-rendered actions, so the first refresh preserves open controls.
  for (const actions of list.querySelectorAll('.message-actions')) {
    actions.dataset.mode = actions.querySelector('details') ? 'moderate' : actions.querySelector('a') ? 'original' : '';
  }
  const request = async (url, options = {}) => {
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 15000);
    try { return await fetch(url, {...options, headers: {Accept: 'application/json'}, credentials: 'same-origin', cache: 'no-store', signal: controller.signal}); }
    finally { clearTimeout(timeout); }
  };
  const schedule = () => {
    clearTimeout(timer);
    if (!stopped && !document.hidden) timer = setTimeout(poll, delay);
  };
  const poll = async () => {
    if (stopped || document.hidden) return;
    const ids = [...list.querySelectorAll('[data-message-id]')].map(row => row.dataset.messageId);
    const chunks = ids.length ? Array.from({length: Math.ceil(ids.length / 50)}, (_, i) => ids.slice(i * 50, i * 50 + 50)) : [[]];
    try {
      for (const chunk of chunks) {
        let more;
        do {
          const url = new URL(chat.dataset.changesUrl, location.href);
          url.searchParams.set('changes_after', cursor);
          url.searchParams.set('visible', chunk.join(','));
          const response = await request(url);
          if (denied(response)) { loseAccess(); return; }
          if (!response.ok) throw new Error('poll');
          const data = await response.json();
          if (stopped) return;
          applyState(data.state);
          data.refresh.forEach(applyMessage);
          data.messages.forEach(applyMessage);
          cursor = data.cursor;
          more = data.more;
        } while (more && !stopped && !document.hidden);
      }
      notice.textContent = '';
      delay = 10000;
    } catch (_) {
      if (!stopped) { notice.textContent = 'Не удалось обновить чат. Повторим автоматически.'; delay = Math.min(delay * 2, 60000); }
    } finally { schedule(); }
  };
  document.addEventListener('visibilitychange', () => { clearTimeout(timer); if (!document.hidden && !stopped) poll(); });
  const form = document.getElementById('chat-send');
  if (form) form.addEventListener('submit', async event => {
    event.preventDefault();
    const button = form.querySelector('button[type=submit]');
    if (button.disabled || stopped) return;
    button.disabled = true;
    try {
      const response = await request(form.action, {method: 'POST', body: new FormData(form)});
      if ([401, 404].includes(response.status)) { loseAccess(); return; }
      const data = await response.json();
      if (!response.ok) {
        notice.textContent = data.error || 'Отправка не выполнена.';
        return;
      }
      if (stopped) return;
      applyMessage(data.message);
      form.querySelector('[name=text]').value = '';
      form.querySelector('[name=request_key]').value = crypto.randomUUID();
      notice.textContent = 'Сообщение отправлено.';
    } catch (_) {
      if (!stopped) notice.textContent = 'Отправка не подтверждена. Повторите: сохраненный ключ предотвращает дубли.';
    } finally { button.disabled = false; }
  });
  schedule();
})();
