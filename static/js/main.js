document.addEventListener('DOMContentLoaded', function () {
  // Sidebar toggle
  const sidebar = document.getElementById('sidebar');
  const mainContent = document.querySelector('.main-content');
  const toggleBtn = document.getElementById('toggleSidebar');

  if (toggleBtn && sidebar) {
    toggleBtn.addEventListener('click', function () {
      if (window.innerWidth <= 768) {
        sidebar.classList.toggle('open');
      } else {
        sidebar.classList.toggle('collapsed');
        mainContent.classList.toggle('expanded');
      }
    });
  }

  // Auto-dismiss alerts after 5 seconds
  document.querySelectorAll('.alert.alert-success, .alert.alert-danger').forEach(function (alert) {
    setTimeout(function () {
      const bsAlert = bootstrap.Alert.getOrCreateInstance(alert);
      if (bsAlert) bsAlert.close();
    }, 5000);
  });

  // Auto-refresh dashboard every 30 seconds if running projects exist
  const isDashboard = document.querySelector('[data-page="dashboard"]');
  if (isDashboard) {
    setTimeout(function () { location.reload(); }, 30000);
  }
});
