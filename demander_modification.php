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

require_once 'db.php';
require_once 'student_helpers.php';

function respondModification(int $status, array $data): void
{
    http_response_code($status);
    echo json_encode($data, JSON_UNESCAPED_UNICODE);
    exit;
}

$data = json_decode(file_get_contents('php://input'), true);
if (!is_array($data)
    || empty($data['matricule'])
    || !is_string($data['matricule'])) {
    respondModification(400, array('status' => 'error', 'message' => 'Matricule requis.'));
}

$editable = array('nom', 'prenoms', 'date_naissance', 'filiere', 'niveau', 'telephone', 'email', 'sexe', 'allergies_affection');
$changes = array();
foreach ($editable as $field) {
    if (!array_key_exists($field, $data)) {
        continue;
    }
    if (!is_string($data[$field])) {
        respondModification(422, array('status' => 'error', 'message' => "Valeur invalide pour $field."));
    }
    $value = trim($data[$field]);
    if ($field === 'nom' || $field === 'prenoms') {
        if ($value === '' || strlen($value) > 150) {
            respondModification(422, array('status' => 'error', 'message' => 'Le nom et les prénoms sont obligatoires (150 caractères maximum).'));
        }
        $changes[$field] = $value;
    } elseif ($field === 'date_naissance') {
        $parsed = parseStudentDate($value);
        if (!$parsed) {
            respondModification(422, array('status' => 'error', 'message' => 'Date de naissance invalide.'));
        }
        $changes[$field] = $parsed;
    } elseif ($field === 'filiere' || $field === 'niveau') {
        if (strlen($value) > 150) {
            respondModification(422, array('status' => 'error', 'message' => "Le champ $field est trop long."));
        }
        $changes[$field] = $value === '' ? null : $value;
    } elseif ($field === 'email') {
        if ($value !== '' && !filter_var($value, FILTER_VALIDATE_EMAIL)) {
            respondModification(422, array('status' => 'error', 'message' => 'Adresse e-mail invalide.'));
        }
        $changes[$field] = $value === '' ? null : $value;
    } elseif ($field === 'sexe') {
        $value = strtoupper($value);
        if ($value !== '' && !in_array($value, array('M', 'F'), true)) {
            respondModification(422, array('status' => 'error', 'message' => 'Le sexe doit être M ou F.'));
        }
        $changes[$field] = $value === '' ? null : $value;
    } else {
        $maxLength = $field === 'allergies_affection' ? 2000 : 40;
        if (strlen($value) > $maxLength) {
            respondModification(422, array('status' => 'error', 'message' => "Le champ $field est trop long."));
        }
        $changes[$field] = $value === '' ? null : $value;
    }
}

if (!$changes) {
    respondModification(422, array('status' => 'error', 'message' => 'Aucune modification proposée.'));
}

try {
    $stmt = $pdo->prepare(
        'SELECT e.id, e.nom, e.prenoms, e.date_naissance, e.telephone, e.email, e.sexe,
                e.allergies_affection, f.libelle AS filiere, n.libelle AS niveau
         FROM etudiant e
         LEFT JOIN filiere f ON e.filiere_id = f.id
         LEFT JOIN niveau n ON e.niveau_id = n.id
         WHERE e.matricule = :matricule'
    );
    $stmt->execute(array(':matricule' => strtoupper(trim($data['matricule']))));
    $student = $stmt->fetch();
    if (!$student) {
        respondModification(404, array('status' => 'error', 'message' => 'Matricule non reconnu.'));
    }

    $different = array();
    foreach ($changes as $field => $value) {
        if ((string) ($student[$field] ?? '') !== (string) ($value ?? '')) {
            $different[$field] = $value;
        }
    }
    if (!$different) {
        respondModification(422, array('status' => 'error', 'message' => 'Aucune différence à soumettre.'));
    }

    $stmt = $pdo->prepare(
        "INSERT INTO etudiant_modification (etudiant_id, modifications)
         VALUES (:etudiant_id, CAST(:modifications AS JSONB))"
    );
    $stmt->execute(array(
        ':etudiant_id' => (int) $student['id'],
        ':modifications' => json_encode($different, JSON_UNESCAPED_UNICODE)
    ));

    respondModification(201, array(
        'status' => 'success',
        'message' => 'Votre demande a été transmise. Les changements seront appliqués après validation par un administrateur.'
    ));
} catch (PDOException $exception) {
    if ($exception->getCode() === '23505') {
        respondModification(409, array('status' => 'error', 'message' => 'Une demande est déjà en attente pour ce dossier.'));
    }
    error_log('Demande de modification UPB : ' . $exception->getMessage());
    respondModification(500, array('status' => 'error', 'message' => 'Erreur lors de l’enregistrement de la demande.'));
}
