'use strict';
if (window.lucide) lucide.createIcons();
document.querySelectorAll('form[data-confirm]').forEach(form => {
  form.addEventListener('submit', event => { if (!confirm(form.dataset.confirm)) event.preventDefault(); });
});
document.querySelectorAll('[data-reveal]').forEach(button => {
  button.addEventListener('click', () => {
    const input = document.getElementById(button.dataset.reveal);
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    button.innerHTML = '<i data-lucide="' + (show ? 'eye-off' : 'eye') + '"></i>';
    if (window.lucide) lucide.createIcons();
    button.title = show ? document.body.dataset.hidePassword : document.body.dataset.showPassword;
    button.setAttribute('aria-label', button.title);
  });
});
const roleSelect = document.getElementById('account-role');
if (roleSelect) {
  const syncRole = () => { document.querySelectorAll('[data-roles]').forEach(section => {
    const visible = section.dataset.roles.split(' ').includes(roleSelect.value);
    section.hidden = !visible;
    section.querySelectorAll('input,select').forEach(input => { input.disabled = !visible; });
  }); syncCatalogs(); };
  roleSelect.addEventListener('change', syncRole);
  syncRole();
}
function syncCatalogs() {
  document.querySelectorAll('[data-inline-catalog]').forEach(select => {
    const section = document.getElementById(select.dataset.inlineCatalog);
    const visible = select.value === 'new' && !select.disabled;
    section.hidden = !visible;
    section.querySelectorAll('input').forEach(input => { input.disabled = !visible; });
  });
}
document.querySelectorAll('[data-inline-catalog]').forEach(select => select.addEventListener('change', syncCatalogs));
syncCatalogs();
const groupSelect = document.getElementById('group-select');
if (groupSelect) {
  const course = document.getElementById('student-course');
  const mode = document.getElementById('study-mode');
  const groupName = document.querySelector('[name=new_group]');
  const admission = document.querySelector('[name=admission_year]');
  const syncCourse = () => {
    const suffix = groupName.value.match(/(\d{4}|\d{2})$/);
    const inferred = suffix ? Number(suffix[1]) + (suffix[1].length === 2 ? 2000 : 0) : 0;
    const year = groupSelect.value === 'new' ? Number(admission.value) || inferred : Number(groupSelect.selectedOptions[0]?.dataset.year);
    course.readOnly = !!year;
    if (year) course.value = Math.max(1, Math.min(Number(course.dataset.academicYear) - year + 1, mode.value === 'part_time' ? 5 : 4));
  };
  [groupSelect, mode, groupName, admission].forEach(input => input.addEventListener('input', syncCourse));
  syncCourse();
}
document.querySelectorAll('[data-selection-form]').forEach(form => {
  const all = form.querySelector('[data-select-all]');
  const boxes = [...form.querySelectorAll('[name=user_ids]:not(:disabled)')];
  const sync = () => {
    const count = boxes.filter(box => box.checked).length;
    form.querySelector('[data-selection-count]').textContent = document.body.dataset.selectedLabel + ' ' + count;
    form.querySelector('[data-selection-submit]').disabled = count === 0;
    all.checked = count > 0 && count === boxes.length;
    all.indeterminate = count > 0 && count < boxes.length;
  };
  all.addEventListener('change', () => { boxes.forEach(box => { box.checked = all.checked; }); sync(); });
  boxes.forEach(box => box.addEventListener('change', sync));
  sync();
});
const notificationLink = document.querySelector('[data-notifications-url]');
if (notificationLink) {
  setInterval(async () => {
    if (document.hidden) return;
    try {
      const response = await fetch(notificationLink.dataset.notificationsUrl, {credentials: 'same-origin'});
      if (!response.headers.get('content-type')?.includes('application/json')) return;
      const data = await response.json();
      if (response.status === 503 && data.maintenance) { window.location.reload(); return; }
      if (!response.ok) return;
      document.querySelectorAll('[data-notification-count]').forEach(badge => { badge.textContent = data.unread || ''; });
    } catch (_) { /* Retry on the next interval after network recovery. */ }
  }, 30000);
}
const serviceStatus = document.querySelector('[data-service-status]');
document.querySelectorAll('[data-database-filter]').forEach(form => {
  const scope = form.querySelector('[data-cleanup-scope]');
  const sync = () => form.querySelectorAll('[data-cleanup-value]').forEach(label => {
    const active = label.dataset.cleanupValue === scope.value;
    label.hidden = !active;
    label.querySelector('select').disabled = !active;
    label.querySelector('select').required = active;
  });
  scope.addEventListener('change', sync);
  sync();
});
const auditDialog = document.querySelector('[data-audit-export]');
if (auditDialog) {
  const form = auditDialog.querySelector('[data-audit-export-form]');
  const dates = auditDialog.querySelector('[data-audit-step=dates]');
  const confirmation = auditDialog.querySelector('[data-audit-step=password]');
  const from = form.elements.from_date;
  const to = form.elements.to_date;
  const password = form.elements.password;
  const showDates = () => {
    dates.hidden = false;
    confirmation.hidden = true;
    password.value = '';
    password.disabled = true;
  };
  document.querySelector('[data-audit-export-open]').addEventListener('click', () => {
    showDates();
    auditDialog.showModal();
  });
  auditDialog.querySelector('[data-audit-close]').addEventListener('click', () => auditDialog.close());
  auditDialog.querySelector('[data-audit-back]').addEventListener('click', showDates);
  auditDialog.addEventListener('close', showDates);
  [from, to].forEach(input => input.addEventListener('input', () => to.setCustomValidity('')));
  auditDialog.querySelector('[data-audit-next]').addEventListener('click', () => {
    to.setCustomValidity('');
    if (!from.reportValidity() || !to.reportValidity()) return;
    const days = (Date.parse(to.value) - Date.parse(from.value)) / 86400000;
    if (!Number.isInteger(days) || days < 0 || days > 6 || to.value > to.max) {
      to.setCustomValidity(auditDialog.dataset.rangeError);
      to.reportValidity();
      return;
    }
    auditDialog.querySelector('[data-audit-period]').textContent = from.value + ' — ' + to.value;
    dates.hidden = true;
    confirmation.hidden = false;
    password.disabled = false;
    password.focus();
  });
}
if (serviceStatus) {
  setInterval(async () => {
    if (document.hidden) return;
    try {
      const response = await fetch(serviceStatus.dataset.serviceStatus, {credentials: 'same-origin'});
      if (response.ok && response.headers.get('content-type')?.includes('application/json') && !(await response.json()).maintenance) window.location.reload();
    } catch (_) { /* Keep the maintenance page during network interruptions. */ }
  }, 30000);
}
