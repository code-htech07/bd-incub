<?php
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: POST, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type, Authorization, X-Requested-With');
header('Content-Type: application/json; charset=UTF-8');

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    http_response_code(200);
    exit;
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    echo json_encode(array('status' => 'error', 'message' => 'Méthode non autorisée.'));
    exit;
}

require_once 'admin_auth.php';
requireAdmin();
require_once 'db.php';
require_once 'student_helpers.php';

function respondImport(int $status, array $data): void
{
    http_response_code($status);
    echo json_encode($data, JSON_UNESCAPED_UNICODE);
    exit;
}

if (!isset($_FILES['fichier']) || $_FILES['fichier']['error'] !== UPLOAD_ERR_OK) {
    respondImport(400, array('status' => 'error', 'message' => 'Sélectionnez un fichier CSV valide.'));
}

$file = $_FILES['fichier'];
if ($file['size'] > 5 * 1024 * 1024 || strtolower(pathinfo($file['name'], PATHINFO_EXTENSION)) !== 'csv') {
    respondImport(422, array('status' => 'error', 'message' => 'Le fichier doit être un CSV de 5 Mo maximum.'));
}

$handle = fopen($file['tmp_name'], 'rb');
if ($handle === false) {
    respondImport(400, array('status' => 'error', 'message' => 'Impossible de lire le fichier CSV.'));
}

$headerLine = fgets($handle);
if ($headerLine === false) {
    fclose($handle);
    respondImport(422, array('status' => 'error', 'message' => 'Le fichier CSV est vide.'));
}

$headerLine = preg_replace('/^\xEF\xBB\xBF/', '', $headerLine);
$semicolonHeader = str_getcsv(rtrim($headerLine, "\r\n"), ';');
$commaHeader = str_getcsv(rtrim($headerLine, "\r\n"), ',');
$delimiter = count($semicolonHeader) >= count($commaHeader) ? ';' : ',';
$headers = $delimiter === ';' ? $semicolonHeader : $commaHeader;
$headers = array_map(function ($value) {
    $ascii = iconv('UTF-8', 'ASCII//TRANSLIT//IGNORE', trim((string) $value));
    return strtolower(preg_replace('/[^a-z0-9]+/', '_', $ascii === false ? '' : $ascii));
}, $headers);

$aliases = array(
    'prenom' => 'prenoms',
    'prénom' => 'prenoms',
    'date_de_naissance' => 'date_naissance',
    'date_naiss' => 'date_naissance',
    'telephone' => 'telephone',
    'téléphone' => 'telephone',
    'email' => 'email',
    'e_mail' => 'email',
    'filiere' => 'filiere',
    'niveau' => 'niveau',
    'allergies' => 'allergies_affection',
    'allergie_affection' => 'allergies_affection',
    'allergies_affections' => 'allergies_affection',
);
foreach ($headers as &$header) {
    if (isset($aliases[$header])) {
        $header = $aliases[$header];
    }
}
unset($header);

$required = array('nom', 'prenoms', 'date_naissance', 'filiere', 'niveau');
if (array_diff($required, $headers)) {
    fclose($handle);
    respondImport(422, array(
        'status' => 'error',
        'message' => 'Colonnes obligatoires : nom, prenoms, date_naissance, filiere, niveau.'
    ));
}

$errors = array();
$matricules = array();
$referenceCache = array('filiere' => array(), 'niveau' => array());
$findReferenceId = function (string $table, string $label) use ($pdo, &$referenceCache): ?int {
    $key = strtolower(trim($label));
    if (!array_key_exists($key, $referenceCache[$table])) {
        $referenceCache[$table][$key] = lookupReferenceId($pdo, $table, $label);
    }
    return $referenceCache[$table][$key];
};
$rowNumber = 1;
$imported = 0;

try {
    $pdo->beginTransaction();
    while (($values = fgetcsv($handle, 0, $delimiter)) !== false) {
        $rowNumber++;
        if ($values === array(null) || count(array_filter($values, function ($value) {
            return trim((string) $value) !== '';
        })) === 0) {
            continue;
        }
        if ($rowNumber > 5001) {
            $errors[] = 'Le fichier dépasse la limite de 5 000 lignes.';
            break;
        }

        $row = array();
        foreach ($headers as $index => $key) {
            $row[$key] = trim((string) ($values[$index] ?? ''));
        }

        $nom = $row['nom'] ?? '';
        $prenoms = $row['prenoms'] ?? '';
        $dateNaissance = parseStudentDate($row['date_naissance'] ?? '');
        $filiere = $row['filiere'] ?? '';
        $niveau = $row['niveau'] ?? '';
        $email = $row['email'] ?? '';
        $sexe = strtoupper($row['sexe'] ?? '');
        $telephone = $row['telephone'] ?? '';
        $allergies = $row['allergies_affection'] ?? '';

        if ($nom === '' || $prenoms === '' || !$dateNaissance || $filiere === '' || $niveau === '') {
            $errors[] = "Ligne $rowNumber : nom, prénoms, date de naissance, filière et niveau sont obligatoires.";
            continue;
        }
        if ($email !== '' && !filter_var($email, FILTER_VALIDATE_EMAIL)) {
            $errors[] = "Ligne $rowNumber : adresse e-mail invalide.";
            continue;
        }
        if ($sexe !== '' && !in_array($sexe, array('M', 'F'), true)) {
            $errors[] = "Ligne $rowNumber : le sexe doit être M ou F.";
            continue;
        }

        try {
            $matricule = studentMatricule($nom, $prenoms, $dateNaissance);
        } catch (InvalidArgumentException $exception) {
            $errors[] = "Ligne $rowNumber : " . $exception->getMessage();
            continue;
        }

        $stmt = $pdo->prepare('SELECT id FROM etudiant WHERE matricule = :matricule');
        $stmt->execute(array(':matricule' => $matricule));
        if ($stmt->fetchColumn() !== false || isset($matricules[$matricule])) {
            $errors[] = "Ligne $rowNumber : matricule déjà présent ou dupliqué dans le CSV ($matricule).";
            continue;
        }

        $filiereId = $findReferenceId('filiere', $filiere);
        $niveauId = $findReferenceId('niveau', $niveau);
        $stmt = $pdo->prepare(
            'INSERT INTO etudiant
                (matricule, nom, prenoms, date_naissance, telephone, email, sexe,
                 allergies_affection, filiere_id, niveau_id)
             VALUES
                (:matricule, :nom, :prenoms, :date_naissance, :telephone, :email, :sexe,
                 :allergies_affection, :filiere_id, :niveau_id)'
        );
        $stmt->execute(array(
            ':matricule' => $matricule,
            ':nom' => $nom,
            ':prenoms' => $prenoms,
            ':date_naissance' => $dateNaissance,
            ':telephone' => $telephone === '' ? null : $telephone,
            ':email' => $email === '' ? null : $email,
            ':sexe' => $sexe === '' ? null : $sexe,
            ':allergies_affection' => $allergies === '' ? null : $allergies,
            ':filiere_id' => $filiereId,
            ':niveau_id' => $niveauId,
        ));
        $matricules[$matricule] = true;
        $imported++;
    }

    fclose($handle);
    if ($errors) {
        $pdo->rollBack();
        respondImport(422, array(
            'status' => 'error',
            'message' => 'Aucune ligne importée : corrigez les erreurs puis réessayez.',
            'errors' => array_slice($errors, 0, 50)
        ));
    }
    if ($imported === 0) {
        $pdo->rollBack();
        respondImport(422, array('status' => 'error', 'message' => 'Le fichier ne contient aucun étudiant à importer.'));
    }

    $pdo->commit();
    respondImport(201, array(
        'status' => 'success',
        'message' => "$imported étudiant(s) importé(s) avec succès.",
        'imported' => $imported,
        'matricules' => array_keys($matricules)
    ));
} catch (PDOException $exception) {
    if (is_resource($handle)) {
        fclose($handle);
    }
    if ($pdo->inTransaction()) {
        $pdo->rollBack();
    }
    error_log('Import CSV UPB : ' . $exception->getMessage());
    respondImport(500, array('status' => 'error', 'message' => "Erreur lors de l'import. Vérifiez le format CSV et le schéma de la base."));
}
