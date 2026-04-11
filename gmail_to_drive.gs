/**
 * Mercadata - Gmail to Google Drive
 * 
 * Copia automáticamente los PDFs de tickets de Mercadona que llegan al Gmail
 * a una carpeta de Google Drive, para que la app Streamlit los procese.
 *
 * INSTRUCCIONES DE CONFIGURACIÓN:
 * 1. Abre https://script.google.com y crea un nuevo proyecto
 * 2. Pega este código
 * 3. Reemplaza DRIVE_FOLDER_ID con el ID de tu carpeta de Drive
 *    (es la parte final de la URL de la carpeta: drive.google.com/drive/folders/ESTE_ID)
 * 4. Guarda y ejecuta checkMercadonaMail() una vez manualmente para dar permisos
 * 5. Ve a Editar → Activadores → Añadir activador:
 *    - Función: checkMercadonaMail
 *    - Tipo de evento: Basado en tiempo → Temporizador por horas → Cada hora
 */

var DRIVE_FOLDER_ID = "ID_DE_TU_CARPETA_DE_DRIVE"; // ← Reemplazar

function checkMercadonaMail() {
  var folder = DriveApp.getFolderById(DRIVE_FOLDER_ID);
  var threads = GmailApp.search("from:noreply@mercadona.es is:unread has:attachment");

  threads.forEach(function(thread) {
    thread.getMessages().forEach(function(msg) {
      msg.getAttachments().forEach(function(att) {
        if (att.getContentType() === "application/pdf") {
          var timestamp = Utilities.formatDate(
            msg.getDate(), "Europe/Madrid", "yyyyMMdd_HHmmss"
          );
          var fileName = "ticket_" + timestamp + "_" + att.getName();
          // Evitar duplicados: comprobar si ya existe un archivo con ese nombre
          var existing = folder.getFilesByName(fileName);
          if (!existing.hasNext()) {
            folder.createFile(att.copyBlob().setName(fileName));
            Logger.log("Ticket guardado: " + fileName);
          } else {
            Logger.log("Ya existe, omitido: " + fileName);
          }
        }
      });
      thread.markRead();
    });
  });

  Logger.log("Comprobación completada. Tickets procesados: " + threads.length);
}
