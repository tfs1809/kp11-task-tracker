(() => {
  document.querySelectorAll('[data-confirm-delete]').forEach(form => {
    form.addEventListener('submit', event => {
      if (!window.confirm(form.dataset.confirmDelete)) event.preventDefault();
    });
  });

  const deleteDialog = document.querySelector('[data-delete-dialog]');
  document.querySelectorAll('[data-delete-dialog-open]').forEach(button => {
    button.addEventListener('click', () => {
      if (typeof deleteDialog?.showModal === 'function') deleteDialog.showModal();
      else deleteDialog?.setAttribute('open', '');
    });
  });
  const closeDeleteDialog = () => {
    if (typeof deleteDialog?.close === 'function') deleteDialog.close();
    else deleteDialog?.removeAttribute('open');
  };
  document.querySelectorAll('[data-delete-dialog-close]').forEach(button => {
    button.addEventListener('click', closeDeleteDialog);
  });
  deleteDialog?.addEventListener('click', event => {
    if (event.target === deleteDialog) closeDeleteDialog();
  });

  const dropdowns = [...document.querySelectorAll('details.assignee-picker')];
  const closeDropdownsOutside = event => {
    dropdowns.forEach(dropdown => {
      if (dropdown.open && !dropdown.contains(event.target)) dropdown.open = false;
    });
  };
  document.addEventListener('click', closeDropdownsOutside);
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    dropdowns.forEach(dropdown => { dropdown.open = false; });
  });

  const systemSearch = document.querySelector('[data-system-search]');
  systemSearch?.addEventListener('input', () => {
    const query = systemSearch.value.trim().toLocaleLowerCase();
    document.querySelectorAll('[data-system-user]').forEach(row => {
      row.hidden = query && !row.dataset.searchText.toLocaleLowerCase().includes(query);
    });
  });

  document.querySelectorAll('[data-password-toggle]').forEach(button => {
    const input = document.getElementById(button.dataset.passwordToggle);
    if (!input) return;
    button.addEventListener('click', () => {
      const visible = input.type === 'text';
      input.type = visible ? 'password' : 'text';
      button.textContent = visible ? '◉' : '◌';
      button.setAttribute('aria-label', visible ? 'Показать пароль' : 'Скрыть пароль');
    });
  });

  document.querySelectorAll('.interface-filters select').forEach(select => {
    select.addEventListener('change', () => select.form?.requestSubmit());
  });
  const container = document.querySelector('[data-subtasks]');
  const kind = document.querySelector('#kind');
  const eventFields = document.querySelector('.event-fields');
  const syncEventFields = () => {
    if (!kind || !eventFields) return;
    const event = kind.value === 'event';
    eventFields.hidden = !event;
    eventFields.querySelectorAll('input').forEach(input => { input.required = event; });
  };
  kind?.addEventListener('change', syncEventFields);
  syncEventFields();
  const add = document.querySelector('[data-add-subtask]');
  const subtaskCount = document.querySelector('[data-subtask-count]');
  const updateSubtaskCount = () => {
    if (subtaskCount) subtaskCount.textContent = container.querySelectorAll('input[name="subtasks"]').length ? `Добавлено: ${container.querySelectorAll('input[name="subtasks"]').length}` : '';
  };
  const wireRemove = button => button.addEventListener('click', () => { button.closest('.subtask-row')?.remove(); updateSubtaskCount(); });
  container?.querySelectorAll('.remove-subtask').forEach(wireRemove);
  if (!container || !add) return;
  const addRow = (value = '') => {
    const row = document.createElement('div');
    row.className = 'subtask-row';
    const id = document.createElement('input'); id.type = 'hidden'; id.name = 'subtask_ids'; id.value = '';
    const box = document.createElement('span'); box.className = 'subtask-box'; box.setAttribute('aria-hidden', 'true');
    const input = document.createElement('input');
    input.name = 'subtasks'; input.maxLength = 1000; input.placeholder = 'Текст подзадачи'; input.value = value;
    const remove = document.createElement('button');
    remove.type = 'button'; remove.className = 'remove-subtask'; remove.setAttribute('aria-label', 'Удалить подзадачу'); remove.textContent = '×';
    wireRemove(remove); row.append(id, box, input, remove); container.append(row); updateSubtaskCount(); input.focus();
  };
  add.addEventListener('click', () => { if (container.querySelectorAll('input[name=subtasks]').length < 50) addRow(); });
  document.querySelectorAll('input[name="assignee_ids"]').forEach(input => input.addEventListener('change', updateAssignees));
  function updateAssignees() {
    const selected = [...document.querySelectorAll('input[name="assignee_ids"]:checked')];
    const label = document.querySelector('[data-assignee-label]');
    const count = document.querySelector('[data-assignee-count]');
    if (label) label.textContent = selected.length ? `${selected.length} выбрано` : 'Выберите исполнителей';
    if (count) count.textContent = selected.length ? `Получателей: ${selected.length}` : 'Выберите одного или нескольких сотрудников';
  }
  updateAssignees(); updateSubtaskCount();
})();
