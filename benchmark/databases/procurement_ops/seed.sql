INSERT INTO suppliers VALUES (1,'Atlas Supply','North',true,'{"tier":"gold"}'),(2,'Beacon Materials','South',true,'{"tier":"silver"}'),(3,'Cedar Components','East',false,'{"tier":"gold"}'),(4,'Delta Industrial','West',true,'{"tier":"bronze"}');
INSERT INTO requisitions VALUES (101,'IT','2026-06-02','approved',120,'{"risk_score":"40"}'),(102,'Facilities','2026-06-04','pending',450,'{"risk_score":"80"}'),(103,'IT','2026-05-20','approved',300,'{"risk_score":"75"}'),(104,'Finance','2026-06-15','approved',700,'{"risk_score":"20"}');
INSERT INTO purchase_orders VALUES (201,1,'2026-06-01','open'),(202,1,'2026-05-01','closed'),(203,2,'2026-06-10','open'),(204,3,'2026-05-10','closed');
INSERT INTO po_lines VALUES (301,201,'SKU-A',10,12.50),(302,202,'SKU-B',4,20),(303,203,'SKU-C',5,40),(304,204,'SKU-D',2,15);
INSERT INTO receipts VALUES (401,301,'2026-06-03 09:00+00',6),(402,301,'2026-06-05 09:00+00',2),(403,302,'2026-05-04 09:00+00',4),(404,303,'2026-06-12 09:00+00',5);
INSERT INTO approvals VALUES (501,101,'2026-06-03 09:00+00','approved'),(502,103,'2026-05-21 09:00+00','approved'),(503,104,'2026-06-16 09:00+00','approved'),(504,102,'2026-06-05 09:00+00','rejected');
INSERT INTO external_directory VALUES (601,1,'owner@example.invalid');
