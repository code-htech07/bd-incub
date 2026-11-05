<?php
header("Access-Control-Allow-Origin: *");
header("Content-Type: application/json; charset=UTF-8");
header("Cache-Control: no-store");
header("Access-Control-Allow-Methods: GET, OPTIONS");
header("Access-Control-Allow-Headers: Content-Type, Authorization, X-Requested-With");

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    http_response_code(200);
    exit;
}

if ($_SERVER['REQUEST_METHOD'] !== 'GET') {
    http_response_code(405);
    echo json_encode(array("status" => "error", "message" => "Méthode non autorisée."));
    exit;
}

require_once 'admin_auth.php';
requireAdmin();
require_once 'db.php';

// Certaines bases renvoient de l'UTF-8 doublement encodé (Ã© au lieu de é)
function fixEncoding($value) {
    if (!is_string($value)) return $value;
    if (preg_match('/[\xC3\x83][\xA2-\xAF]/u', $value)) {
        return utf8_encode($value);
    }
    return $value;
}

$config = require 'config.php';
$defaultPerPage = (int) $config['default_per_page'];
$maxPerPage     = (int) $config['max_per_page'];

// Paramètres de pagination et de recherche
$page   = max(1, (int) ($_GET['page'] ?? 1));
$perPage = min($maxPerPage, max(1, (int) ($_GET['per_page'] ?? $defaultPerPage)));
$offset = ($page - 1) * $perPage;
$search = trim($_GET['q'] ?? '');

try {
    $base = "FROM etudiant e
             LEFT JOIN filiere f ON e.filiere_id = f.id
             LEFT JOIN niveau n ON e.niveau_id = n.id
             LEFT JOIN candidat_concours c ON e.id = c.etudiant_id";

    $where = '';
    $params = [];

    if ($search !== '') {
        $where = "WHERE e.matricule ILIKE :search
                  OR e.nom ILIKE :search
                  OR e.prenoms ILIKE :search
                  OR f.libelle ILIKE :search";
        $params[':search'] = '%' . $search . '%';
    }

    $countSql = "SELECT COUNT(*) AS total $base $where";
    $stmtCount = $pdo->prepare($countSql);
    foreach ($params as $k => $v) {
        $stmtCount->bindValue($k, $v, PDO::PARAM_STR);
    }
    $stmtCount->execute();
    $total = (int) $stmtCount->fetchColumn();

    $dataSql = "SELECT e.id, e.matricule, e.nom, e.prenoms, e.date_naissance,
                       e.telephone, e.email, e.sexe, e.allergies_affection,
                       f.libelle AS filiere, n.libelle AS niveau,
                       c.numero_candidat, c.statut
                $base $where
                ORDER BY e.nom ASC
                LIMIT :limit OFFSET :offset";
    $stmt = $pdo->prepare($dataSql);
    foreach ($params as $k => $v) {
        $stmt->bindValue($k, $v, PDO::PARAM_STR);
    }
    $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
    $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
    $stmt->execute();
    $etudiants = $stmt->fetchAll();
    array_walk_recursive($etudiants, function (&$v) { $v = fixEncoding($v); });

    $totalPages = (int) ceil($total / $perPage);

    // Statistiques globales : indépendantes de la pagination et de la recherche
    $stats = array(
        'total'      => 0,
        'inscrits'   => 0,
        'telephones' => 0,
        'emails'     => 0,
        'filieres'   => array(),
        'niveaux'    => array(),
    );

    try {
        $stats['total']      = (int) $pdo->query("SELECT COUNT(*) FROM etudiant")->fetchColumn();
        $stats['inscrits']   = (int) $pdo->query("SELECT COUNT(DISTINCT etudiant_id) FROM candidat_concours")->fetchColumn();
        $stats['telephones'] = (int) $pdo->query("SELECT COUNT(*) FROM etudiant WHERE telephone IS NOT NULL AND TRIM(telephone) <> ''")->fetchColumn();
        $stats['emails']     = (int) $pdo->query("SELECT COUNT(*) FROM etudiant WHERE email IS NOT NULL AND TRIM(email) <> ''")->fetchColumn();

        $stats['filieres'] = $pdo->query(
            "SELECT COALESCE(f.libelle, 'Non renseignée') AS libelle, COUNT(e.id) AS total
             FROM etudiant e
             LEFT JOIN filiere f ON e.filiere_id = f.id
             GROUP BY COALESCE(f.libelle, 'Non renseignée')
             ORDER BY total DESC"
        )->fetchAll();

        $stats['niveaux'] = $pdo->query(
            "SELECT COALESCE(n.libelle, 'Non renseigné') AS libelle, COUNT(e.id) AS total
             FROM etudiant e
             LEFT JOIN niveau n ON e.niveau_id = n.id
             GROUP BY COALESCE(n.libelle, 'Non renseigné')
             ORDER BY total DESC"
        )->fetchAll();

        foreach (array('filieres', 'niveaux') as $key) {
            foreach ($stats[$key] as &$row) {
                $row['libelle'] = fixEncoding($row['libelle']);
                $row['total']   = (int) $row['total'];
            }
            unset($row);
        }
    } catch (PDOException $e) {
        // Sans statistiques, la liste reste affichable
    }

    echo json_encode(array(
        "status"     => "success",
        "data"       => $etudiants,
        "total"      => $total,
        "page"       => $page,
        "per_page"   => $perPage,
        "total_pages"=> $totalPages,
        "stats"      => $stats
    ));

} catch (PDOException $e) {
    http_response_code(500);
    echo json_encode(array("status" => "error", "message" => $e->getMessage()));
}
