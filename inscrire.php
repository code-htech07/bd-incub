<?php
header('Access-Control-Allow-Origin: *');
header('Content-Type: application/json; charset=UTF-8');
header('Access-Control-Allow-Methods: POST, OPTIONS');
header('Access-Control-Allow-Headers: Content-Type, Authorization, X-Requested-With');

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

$data = json_decode(file_get_contents('php://input'), true);
$matricule = isset($data['matricule']) && is_string($data['matricule']) ? strtoupper(trim($data['matricule'])) : '';

if ($matricule === '' || strlen($matricule) > 40) {
    http_response_code(400);
    echo json_encode(array('status' => 'error', 'message' => 'Matricule requis.'));
    exit;
}

try {
    $stmtEtudiant = $pdo->prepare('SELECT id FROM etudiant WHERE matricule = :matricule');
    $stmtEtudiant->execute(array(':matricule' => $matricule));
    $etudiant = $stmtEtudiant->fetch();

    if (!$etudiant) {
        http_response_code(404);
        echo json_encode(array('status' => 'error', 'message' => 'Matricule non reconnu.'));
        exit;
    }

    $etudiantId = (int) $etudiant['id'];
    $stmtCheck = $pdo->prepare(
        'SELECT numero_candidat FROM candidat_concours WHERE etudiant_id = :etudiant_id LIMIT 1'
    );
    $stmtCheck->execute(array(':etudiant_id' => $etudiantId));
    $existing = $stmtCheck->fetch();
    if ($existing) {
        http_response_code(409);
        echo json_encode(array(
            'status' => 'error',
            'message' => 'Cet étudiant est déjà inscrit au concours Génie UPB.',
            'numero_candidat' => $existing['numero_candidat']
        ), JSON_UNESCAPED_UNICODE);
        exit;
    }

    $numeroCandidat = 'GENIE-2026-' . strtoupper(substr(uniqid(), -5));
    $stmtInsert = $pdo->prepare(
        "INSERT INTO candidat_concours (etudiant_id, numero_candidat, statut)
         VALUES (:etudiant_id, :numero_candidat, 'EN_ATTENTE')"
    );
    $stmtInsert->execute(array(
        ':etudiant_id' => $etudiantId,
        ':numero_candidat' => $numeroCandidat
    ));

    http_response_code(201);
    echo json_encode(array(
        'status' => 'success',
        'message' => 'Inscription au concours Génie UPB réussie avec succès !',
        'data' => array(
            'matricule' => $matricule,
            'numero_candidat' => $numeroCandidat,
            'statut' => 'EN_ATTENTE'
        )
    ), JSON_UNESCAPED_UNICODE);
} catch (PDOException $exception) {
    error_log('Inscription au concours UPB : ' . $exception->getMessage());
    http_response_code(500);
    echo json_encode(array('status' => 'error', 'message' => "Erreur lors de l'enregistrement de l'inscription."));
}
