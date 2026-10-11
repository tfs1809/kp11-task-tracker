(() => {
  document.querySelectorAll('.interface-filters select').forEach(select => {
    select.addEventListener('change', () => select.form?.requestSubmit());
  });
  const container = document.querySelector('[data-subtasks]');
  const add = document.querySelector('[data-add-subtask]');
  if (!container || !add) return;
  const addRow = (value = '') => {
    const row = document.createElement('div');
    row.className = 'subtask-row';
    const input = document.createElement('input');
    input.name = 'subtasks'; input.maxLength = 1000; input.placeholder = 'Текст подзадачи'; input.value = value;
    const remove = document.createElement('button');
    remove.type = 'button'; remove.className = 'remove-subtask secondary'; remove.textContent = 'Удалить';
    remove.addEventListener('click', () => row.remove());
    row.append(input, remove); container.append(row); input.focus();
  };
  container.querySelectorAll('.remove-subtask').forEach(button => button.addEventListener('click', () => button.closest('.subtask-row').remove()));
  add.addEventListener('click', () => { if (container.querySelectorAll('input[name=subtasks]').length < 50) addRow(); });
})();
