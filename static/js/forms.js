// Необязательное добавление строк; сервер проверяет количество, UUID и порядок.
const add = document.querySelector('[data-add-subtask]');
if (add) {
  add.hidden = false;
  add.addEventListener('click', () => {
    const total = document.querySelector('#id_subtasks-TOTAL_FORMS');
    const count = Number(total.value);
    if (count >= 50) { add.disabled = true; return; }
    const template = document.querySelector('#empty-subtask');
    const fragment = template.content.cloneNode(true);
    fragment.querySelectorAll('[name], [id], [for]').forEach(element => {
      for (const name of ['name', 'id', 'for']) {
        if (element.hasAttribute(name)) element.setAttribute(name, element.getAttribute(name).replaceAll('__prefix__', String(count)));
      }
    });
    fragment.querySelector('legend').textContent = `Подзадача ${count + 1}`;
    fragment.querySelector('[name$="-position"]').value = count + 1;
    document.querySelector('[data-subtasks]').append(fragment);
    total.value = count + 1;
    document.querySelector(`[name="subtasks-${count}-text"]`).focus();
    if (count + 1 >= 50) add.disabled = true;
  });
}
