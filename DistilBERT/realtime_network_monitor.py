#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import onnxruntime as ort
import numpy as np
import pandas as pd
import pickle
import time
import argparse
import json
import os
import warnings
from datetime import datetime
import threading
import queue
import sys

warnings.filterwarnings('ignore', message='X does not have valid feature names')
warnings.filterwarnings('ignore', category=UserWarning, module='sklearn')

class NetworkAttackDetector:
    def __init__(self, model_path, metadata_path, confidence_threshold=0.8):
        
        print("Carregando modelo...")
        self.session = ort.InferenceSession(model_path)
        
        print("Carregando metadados...")
        with open(metadata_path, 'rb') as f:
            self.metadata = pickle.load(f)
        
        self.scaler = self.metadata['scaler']
        self.label_encoder = self.metadata['label_encoder']
        self.feature_names = self.metadata['feature_names']
        self.classes = self.metadata['classes']
        
        # Configurar threshold de confiança
        self.confidence_threshold = confidence_threshold
        
        # Definir classes consideradas menos perigosas (podem ser ajustadas)
        self.low_threat_classes = [
            'VulnerabilityScan',  # Menos crítico que DDoS
            'Recon-PingSweep',    # Reconhecimento básico
            'BrowserHijacking'    # Menos impactante que DDoS
        ]
        
        print(f"Modelo carregado com sucesso!")
        print(f"Classes detectáveis: {self.classes}")
        print(f"Threshold de confiança: {self.confidence_threshold}")
        print(f"Classes de baixa ameaça: {self.low_threat_classes}")
        
        self.total_predictions = 0
        self.attack_detections = 0
        self.inference_times = []
    
    def preprocess_features(self, features_dict):
        
        feature_values = {}
        for feature_name in self.feature_names:
            feature_values[feature_name] = features_dict.get(feature_name, 0.0)
        
        features_df = pd.DataFrame([feature_values])
        
        try:
            features_scaled = self.scaler.transform(features_df)
        except Exception as e:
            print(f"Aviso: Erro na normalização, usando dados sem normalização: {e}")
            features_scaled = features_df.values
        
        return features_scaled.astype(np.float32)
    
    def predict(self, features_dict, verbose=True):
        
        features = self.preprocess_features(features_dict)
        
        start_time = time.time()
        ort_inputs = {'features': features}
        logits, probabilities = self.session.run(None, ort_inputs)
        inference_time = (time.time() - start_time) * 1000
        
        predicted_class_idx = np.argmax(probabilities[0])
        predicted_class = self.classes[predicted_class_idx]
        confidence = probabilities[0][predicted_class_idx]
        
        self.total_predictions += 1
        self.inference_times.append(inference_time)

        # CORREÇÃO: Implementar lógica robusta para determinar ataques
        # Verificar se é tráfego benigno primeiro
        is_benign = predicted_class.lower() in ['benigntraffic', 'benign', 'normal']
        
        if is_benign:
            # Tráfego benigno nunca é considerado ataque
            is_critical_attack = False
        else:
            # Método 1: Threshold de confiança
            high_confidence_attack = confidence > self.confidence_threshold
            
            # Método 2: Classificar por severidade
            is_high_threat = predicted_class not in self.low_threat_classes
            
            # Método 3: Threshold dinâmico baseado no tipo de ataque
            ddos_classes = [cls for cls in self.classes if 'DDoS' in cls or 'DoS' in cls]
            is_ddos = predicted_class in ddos_classes
            
            # Lógica final: Considerar ataque se:
            # - Alta confiança E (alta ameaça OU é DDoS)
            # - OU confiança muito alta (>0.9) independente do tipo
            is_critical_attack = (
                (high_confidence_attack and is_high_threat) or
                (high_confidence_attack and is_ddos) or
                (confidence > 0.9)
            )
        
        # Atualizar estatísticas apenas para ataques críticos
        if is_critical_attack:
            self.attack_detections += 1
        
        return {
            'timestamp': datetime.now().isoformat(),
            'predicted_class': predicted_class,
            'confidence': float(confidence),
            'is_attack': is_critical_attack,
            'is_benign': is_benign,
            'is_high_threat': predicted_class not in self.low_threat_classes if not is_benign else False,
            'is_ddos': predicted_class in [cls for cls in self.classes if 'DDoS' in cls or 'DoS' in cls] if not is_benign else False,
            'confidence_threshold': self.confidence_threshold,
            'inference_time_ms': inference_time,
            'all_probabilities': probabilities[0].tolist()
        }
    
    def get_statistics(self):
        
        if not self.inference_times:
            return {}
        
        return {
            'total_predictions': self.total_predictions,
            'attack_detections': self.attack_detections,
            'attack_rate': self.attack_detections / self.total_predictions if self.total_predictions > 0 else 0,
            'avg_inference_time_ms': np.mean(self.inference_times),
            'max_inference_time_ms': np.max(self.inference_times),
            'min_inference_time_ms': np.min(self.inference_times),
            'throughput_per_second': 1000 / np.mean(self.inference_times),
            'confidence_threshold': self.confidence_threshold
        }

class RealTimeMonitor:
    def __init__(self, detector, log_file='attack_log.json', result_file=None):
        self.detector = detector
        self.log_file = log_file
        self.result_file = result_file
        self.data_queue = queue.Queue()
        self.running = False
        self.results = []  
    
    def log_detection(self, result):
        
        with open(self.log_file, 'a') as f:
            f.write(json.dumps(result) + '\n')
    
    def save_result(self, message):
        
        if self.result_file:
            with open(self.result_file, 'a', encoding='utf-8') as f:
                f.write(message + '\n')
        else:
            print(message)
    
    def save_all_results(self):
        
        if self.result_file and self.results:
            with open(self.result_file, 'w', encoding='utf-8') as f:
                f.write("=== RESULTADOS DA ANÁLISE ===\n")
                f.write(f"Data/Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Total de amostras processadas: {len(self.results)}\n")
                f.write(f"Threshold de confiança: {self.detector.confidence_threshold}\n\n")
                
                attacks = [r for r in self.results if r['is_attack']]
                low_confidence = [r for r in self.results if r['confidence'] < self.detector.confidence_threshold]
                high_confidence = [r for r in self.results if r['confidence'] >= self.detector.confidence_threshold]
                
                f.write(f"Ataques críticos detectados: {len(attacks)}\n")
                f.write(f"Taxa de ataques críticos: {len(attacks)/len(self.results)*100:.2f}%\n")
                f.write(f"Atividade normal/baixo risco: {len(self.results) - len(attacks)}\n")
                f.write(f"Predições baixa confiança: {len(low_confidence)}\n")
                f.write(f"Predições alta confiança: {len(high_confidence)}\n\n")
                
                if attacks:
                    attack_types = {}
                    for attack in attacks:
                        attack_type = attack['predicted_class']
                        attack_types[attack_type] = attack_types.get(attack_type, 0) + 1
                    
                    f.write("=== TIPOS DE ATAQUES CRÍTICOS DETECTADOS ===\n")
                    for attack_type, count in sorted(attack_types.items()):
                        f.write(f"{attack_type}: {count} ocorrências\n")
                    f.write("\n")
                
                # Analisar distribuição de confiança
                confidence_levels = [r['confidence'] for r in self.results]
                f.write("=== DISTRIBUIÇÃO DE CONFIANÇA ===\n")
                f.write(f"Confiança média: {np.mean(confidence_levels):.3f}\n")
                f.write(f"Confiança mediana: {np.median(confidence_levels):.3f}\n")
                f.write(f"Confiança mínima: {min(confidence_levels):.3f}\n")
                f.write(f"Confiança máxima: {max(confidence_levels):.3f}\n\n")
                
                f.write("=== DETALHES DAS DETECÇÕES ===\n")
                for i, result in enumerate(self.results, 1):
                    if result['is_attack']:
                        status = "🚨 ATAQUE CRÍTICO"
                    elif result.get('is_benign', False):
                        status = "✅ TRÁFEGO NORMAL"
                    elif result['confidence'] >= self.detector.confidence_threshold:
                        status = "⚠️ ATIVIDADE SUSPEITA"
                    else:
                        status = "🔍 BAIXO RISCO"
                        
                    f.write(f"Amostra {i}: {status}\n")
                    f.write(f"  Classe: {result['predicted_class']}\n")
                    f.write(f"  Confiança: {result['confidence']:.3f}\n")
                    f.write(f"  É tráfego benigno: {result.get('is_benign', 'N/A')}\n")
                    f.write(f"  É DDoS: {result.get('is_ddos', 'N/A')}\n")
                    f.write(f"  Alta ameaça: {result.get('is_high_threat', 'N/A')}\n")
                    f.write(f"  Tempo de inferência: {result['inference_time_ms']:.2f} ms\n")
                    f.write(f"  Timestamp: {result['timestamp']}\n")
                    f.write("\n")
    
    def process_data_stream(self):
        
        while self.running:
            try:
                features_dict = self.data_queue.get(timeout=1)
                
                result = self.detector.predict(features_dict)
                
                self.results.append(result)
                
                if result['is_attack']:
                    message = f"🚨 ATAQUE CRÍTICO: {result['predicted_class']} (Confiança: {result['confidence']:.3f})"
                    self.save_result(message)
                    self.log_detection(result)
                elif result.get('is_benign', False):
                    message = f"✅ TRÁFEGO NORMAL: {result['predicted_class']} (Confiança: {result['confidence']:.3f})"
                    self.save_result(message)
                elif result['confidence'] >= self.detector.confidence_threshold:
                    message = f"⚠️ ATIVIDADE SUSPEITA: {result['predicted_class']} (Confiança: {result['confidence']:.3f})"
                    self.save_result(message)
                else:
                    message = f"🔍 BAIXO RISCO: {result['predicted_class']} (Confiança: {result['confidence']:.3f})"
                    self.save_result(message)
                
                self.data_queue.task_done()
                
            except queue.Empty:
                continue
            except Exception as e:
                error_msg = f"Erro no processamento: {e}"
                self.save_result(error_msg)
    
    def start_monitoring(self):
        
        self.running = True
        monitor_thread = threading.Thread(target=self.process_data_stream)
        monitor_thread.daemon = True
        monitor_thread.start()
        
        print("Monitoramento iniciado...")
        return monitor_thread
    
    def stop_monitoring(self):
        self.running = False
    
    def add_data(self, features_dict):
        self.data_queue.put(features_dict)

def simulate_network_data(csv_file, detector, monitor, delay=1.0):
    message = f"Carregando dados de simulação: {csv_file}"
    monitor.save_result(message)
    df = pd.read_csv(csv_file)
    
    message = f"Iniciando simulação com {len(df)} amostras..."
    monitor.save_result(message)
    
    for idx, row in df.iterrows():
        features_dict = row.drop('label').to_dict()
        
        monitor.add_data(features_dict)
        
        if (idx + 1) % 100 == 0:
            stats = detector.get_statistics()
            progress_msg = f"\nProcessadas {idx + 1} amostras"
            monitor.save_result(progress_msg)
            monitor.save_result(f"Taxa de ataques: {stats.get('attack_rate', 0):.3f}")
            monitor.save_result(f"Tempo médio: {stats.get('avg_inference_time_ms', 0):.2f} ms")
        
        time.sleep(delay)

def main():
    parser = argparse.ArgumentParser(description='Detector de Ataques de Rede em Tempo Real')
    parser.add_argument('--model', default='network_attack_detector_quantized.onnx', help='Modelo ONNX')
    parser.add_argument('--metadata', default='model_metadata.pkl', help='Metadados do modelo')
    parser.add_argument('--simulate', type=str, help='Arquivo CSV para simulação')
    parser.add_argument('--delay', type=float, default=0.1, help='Delay entre amostras (segundos)')
    parser.add_argument('--interactive', action='store_true', help='Modo interativo')
    parser.add_argument('--benchmark', action='store_true', help='Benchmark de performance')
    parser.add_argument('--output', type=str, help='Arquivo de saída personalizado')
    
    args = parser.parse_args()
    
    result_file = None
    if args.simulate:
        csv_basename = os.path.splitext(os.path.basename(args.simulate))[0]
        result_file = f"result-distilbert-part-{csv_basename}.txt"
        print(f"Resultados serão salvos em: {result_file}")
    elif args.output:
        result_file = args.output
        print(f"Resultados serão salvos em: {result_file}")
    
    try:
        detector = NetworkAttackDetector(args.model, args.metadata)
        monitor = RealTimeMonitor(detector, result_file=result_file)
    except Exception as e:
        print(f"Erro ao inicializar detector: {e}")
        sys.exit(1)
    
    if args.benchmark:
        print("Executando benchmark...")
        
        test_features = {name: np.random.randn() for name in detector.feature_names}
        
        for i in range(1000):
            detector.predict(test_features)
        
        stats = detector.get_statistics()
        print(f"\nResultados do benchmark:")
        print(f"Predições: {stats['total_predictions']}")
        print(f"Tempo médio: {stats['avg_inference_time_ms']:.2f} ms")
        print(f"Throughput: {stats['throughput_per_second']:.2f} predições/segundo")
        
    elif args.simulate:
        monitor_thread = monitor.start_monitoring()
        
        try:
            simulate_network_data(args.simulate, detector, monitor, args.delay)
            
            monitor.data_queue.join()
            
        except KeyboardInterrupt:
            monitor.save_result("Interrompido pelo usuário")
        finally:
            monitor.stop_monitoring()
            
            monitor.save_all_results()
            
            stats = detector.get_statistics()
            final_stats = f"\n=== ESTATÍSTICAS FINAIS ==="
            monitor.save_result(final_stats)
            for key, value in stats.items():
                monitor.save_result(f"{key}: {value}")
            
            if result_file:
                print(f"\n✅ Análise concluída! Resultados salvos em: {result_file}")
    
    elif args.interactive:
        print("\nModo interativo ativado.")
        print("Digite valores para as features ou 'sair' para encerrar.")
        print(f"Features necessárias: {detector.feature_names[:5]}... (total: {len(detector.feature_names)})")
        
        while True:
            try:
                input("Pressione Enter para gerar predição com dados aleatórios (ou Ctrl+C para sair): ")
                
                test_features = {name: np.random.randn() for name in detector.feature_names}
                result = detector.predict(test_features)
                
                print(f"\nResultado:")
                print(f"  Classe: {result['predicted_class']}")
                print(f"  Confiança: {result['confidence']:.3f}")
                print(f"  É ataque: {result['is_attack']}")
                print(f"  Tempo: {result['inference_time_ms']:.2f} ms")
                
            except KeyboardInterrupt:
                break
    
    else:
        print("Use --simulate, --interactive ou --benchmark")
        parser.print_help()

if __name__ == '__main__':
    main()
