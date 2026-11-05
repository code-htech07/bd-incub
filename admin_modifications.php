<?php
header('Access-Control-Allow-Origin: *');
header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type, Authorization, X-Requested-With');
header('Content-Type: application/json; charset=UTF-8');
header('Cache-Control: no-store');

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    http_response_code(200);
    exit;
}

require_once 'admin_auth.php';
requireAdmin();
require_once 'db.php';
require_once 'student_helpers.php';

function respondAdminModifications(int $status, array $data): void
{
    http_response_code($status);
    echo json_encode($data, JSON_UNESCAPED_UNICODE);
    exit;
}

if ($_SERVER['REQUEST_METHOD'] === 'GET') {
    try {
        $stmt = $pdo->query(
            "SELECT m.id, m.etudiant_id, m.modifications, m.created_at,
                    e.matricule, e.nom, e.prenoms, e.date_naissance
             FROM etudiant_modification m
             JOIN etudiant e ON e.id = m.etudiant_id
             WHERE m.statut = 'EN_ATTENTE'
             ORDER BY m.created_at ASC"
        );
        $requests = $stmt->fetchAll();
        foreach ($requests as &$request) {
            if (is_string($request['modifications'])) {
                $request['modifications'] = json_decode($request['modifications'], true);
            }
        }
        unset($request);
        echo json_encode(array('status' => 'success', 'data' => $requests), JSON_UNESCAPED_UNICODE);
        exit;
    } catch (PDOException $exception) {
        error_log('Lecture des demandes UPB : ' . $exception->getMessage());
        respondAdminModifications(500, array('status' => 'error', 'message' => 'Impossible de charger les demandes.'));
    }
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    respondAdminModifications(405, array('status' => 'error', 'message' => 'Méthode non autorisée.'));
}

$data = json_decode(file_get_contents('php://input'), true);
$requestId = isset($data['id']) ? filter_var($data['id'], FILTER_VALIDATE_INT) : false;
$action = $data['action'] ?? '';
if (!$requestId || !in_array($action, array('approve', 'reject'), true)) {
    respondAdminModifications(400, array('status' => 'error', 'message' => 'Demande ou action invalide.'));
}

try {
    $pdo->beginTransaction();
    $stmt = $pdo->prepare(
        "SELECT id, etudiant_id, modifications
         FROM etudiant_modification
         WHERE id = :id AND statut = 'EN_ATTENTE'
         FOR UPDATE"
    );
    $stmt->execute(array(':id' => $requestId));
    $request = $stmt->fetch();
    if (!$request) {
        $pdo->rollBack();
        respondAdminModifications(404, array('status' => 'error', 'message' => 'Demande déjà traitée ou introuvable.'));
    }

    if ($action === 'reject') {
        $stmt = $pdo->prepare(
            "UPDATE etudiant_modification
             SET statut = 'REFUSEE', reviewed_at = NOW()
             WHERE id = :id"
        );
        $stmt->execute(array(':id' => $requestId));
        $pdo->commit();
        respondAdminModifications(200, array('status' => 'success', 'message' => 'Demande refusée.'));
    }

    $changes = is_array($request['modifications'])
        ? $request['modifications']
        : json_decode($request['modifications'], true);
    $allowed = array('nom', 'prenoms', 'date_naissance', 'filiere', 'niveau', 'telephone', 'email', 'sexe', 'allergies_affection');
    if (!is_array($changes) || !$changes || array_diff(array_keys($changes), $allowed)) {
        throw new RuntimeException('Contenu de demande invalide.');
    }

    $stmt = $pdo->prepare('SELECT matricule, nom, prenoms, date_naissance FROM etudiant WHERE id = :id FOR UPDATE');
    $stmt->execute(array(':id' => $request['etudiant_id']));
    $student = $stmt->fetch();
    if (!$student) {
        throw new RuntimeException('Étudiant associé à la demande introuvable.');
    }

    $matricule = $student['matricule'];
    if (isset($changes['nom']) || isset($changes['prenoms']) || isset($changes['date_naissance'])) {
        $nom = (string) ($changes['nom'] ?? $student['nom']);
        $prenoms = (string) ($changes['prenoms'] ?? $student['prenoms']);
        $dateNaissance = (string) ($changes['date_naissance'] ?? $student['date_naissance']);
        if ($dateNaissance === '') {
            throw new RuntimeException('Une date de naissance est requise pour recalculer le matricule.');
        }
        try {
            $matricule = studentMatricule($nom, $prenoms, $dateNaissance);
        } catch (InvalidArgumentException $exception) {
            throw new RuntimeException($exception->getMessage());
        }
        $stmt = $pdo->prepare('SELECT id FROM etudiant WHERE matricule = :matricule AND id <> :id');
        $stmt->execute(array(':matricule' => $matricule, ':id' => $request['etudiant_id']));
        if ($stmt->fetchColumn() !== false) {
            throw new RuntimeException('Le matricule calculé existe déjà pour un autre étudiant.');
        }
    }

    if (isset($changes['nom']) || isset($changes['prenoms']) || isset($changes['date_naissance'])) {
        $changes['matricule'] = $matricule;
    }
    if (array_key_exists('filiere', $changes)) {
        $changes['filiere_id'] = $changes['filiere'] === null
            ? null
            : lookupReferenceId($pdo, 'filiere', (string) $changes['filiere']);
        unset($changes['filiere']);
    }
    if (array_key_exists('niveau', $changes)) {
        $changes['niveau_id'] = $changes['niveau'] === null
            ? null
            : lookupReferenceId($pdo, 'niveau', (string) $changes['niveau']);
        unset($changes['niveau']);
    }
    $set = array();
    $params = array(':id' => (int) $request['etudiant_id']);
    foreach ($changes as $field => $value) {
        if ($field === 'date_naissance' && $value !== null) {
            $value = parseStudentDate((string) $value);
            if (!$value) {
                throw new RuntimeException('Date de naissance invalide.');
            }
        }
        if (in_array($field, array('telephone', 'email', 'sexe', 'allergies_affection'), true)
            && $value === '') {
            $value = null;
        }
        $set[] = "$field = :$field";
        $params[":$field"] = $value;
    }

    $stmt = $pdo->prepare('UPDATE etudiant SET ' . implode(', ', $set) . ' WHERE id = :id');
    $stmt->execute($params);
    $stmt = $pdo->prepare(
        "UPDATE etudiant_modification
         SET statut = 'APPROUVEE', reviewed_at = NOW()
         WHERE id = :id"
    );
    $stmt->execute(array(':id' => $requestId));
    $pdo->commit();

    respondAdminModifications(200, array(
        'status' => 'success',
        'message' => 'Demande approuvée et informations mises à jour.',
        'matricule' => $matricule
    ));
} catch (RuntimeException $exception) {
    if ($pdo->inTransaction()) {
        $pdo->rollBack();
    }
    respondAdminModifications(422, array('status' => 'error', 'message' => $exception->getMessage()));
} catch (PDOException $exception) {
    if ($pdo->inTransaction()) {
        $pdo->rollBack();
    }
    error_log('Traitement de demande UPB : ' . $exception->getMessage());
    respondAdminModifications(500, array('status' => 'error', 'message' => 'Erreur lors du traitement de la demande.'));
}
