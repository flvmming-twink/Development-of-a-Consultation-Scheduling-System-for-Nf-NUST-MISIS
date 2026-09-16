'use strict';
document.querySelectorAll('form[data-confirm]').forEach(form => {
  form.addEventListener('submit', event => { if (!confirm(form.dataset.confirm)) event.preventDefault(); });
});
document.querySelectorAll('[data-reveal]').forEach(button => {
  button.addEventListener('click', () => {
    const input = document.getElementById(button.dataset.reveal);
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    button.textContent = show ? 'Скрыть' : 'Показать';
    button.setAttribute('aria-label', show ? 'Скрыть пароль' : 'Показать пароль');
  });
});
const roleSelect = document.getElementById('account-role');
if (roleSelect) {
  const syncRole = () => document.querySelectorAll('[data-roles]').forEach(section => {
    const visible = section.dataset.roles.split(' ').includes(roleSelect.value);
    section.hidden = !visible;
    section.querySelectorAll('input,select').forEach(input => { input.disabled = !visible; });
  });
  roleSelect.addEventListener('change', syncRole);
  syncRole();
}
