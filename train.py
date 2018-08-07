import torch
from torch.utils.data import DataLoader
import torch.backends.cudnn as cudnn
import argparse
import os

from preprocess import preprocess
from models import general_FFTNet
from dataset import CMU_Dataset
from datetime import datetime

parser = argparse.ArgumentParser(description='FFTNet vocoder training.')
parser.add_argument('--preprocess', action='store_true')
parser.add_argument('--wav_dir', type=str, default='/host/data_dsk1/dataset/CMU_ARCTIC_Databases/cmu_us_rms_arctic/wav')
parser.add_argument('--data_dir', type=str, default='training_data')
parser.add_argument('--num_mfcc', type=int, default=25, help='number of mfcc coefficients')
parser.add_argument('--window_length', type=float, default=0.025)
parser.add_argument('--window_step', type=float, default=0.01)
parser.add_argument('--minimum_f0', type=float, default=40)
parser.add_argument('--maximum_f0', type=float, default=500)
parser.add_argument('--q_channels', type=int, default=256, help='quantization channels')
parser.add_argument('--interp_method', type=str, default='linear')
parser.add_argument('--fft_channels', type=int, default=256, help='fftnet layer channels')
parser.add_argument('--seq_M', type=int, default=5000, help='training sequence length')
parser.add_argument('--radixs', nargs='+', type=int, default=[2] * 11)
parser.add_argument('--batch_size', type=int, default=5)
parser.add_argument('--lr', type=float, default=0.001, help='learning rate')
parser.add_argument('--steps', type=int, default=100000, help='iteration number')
parser.add_argument('--noise_injecting', type=bool, default=True)
parser.add_argument('--checkpoint_dir', type=str, default='checkpoints/',
                    help='Directory to save checkpoints.')

sampling_rate = 16000


def main():
    args = parser.parse_args()
    if args.preprocess:
        print('==> Preprocessing data ...')
        preprocess(args.wav_dir, args.data_dir, winlen=args.window_length, winstep=args.window_step,
                   n_mfcc=args.num_mfcc, minf0=args.minimum_f0, maxf0=args.maximum_f0, q_channels=args.q_channels)

    print('==> Loading Dataset..')
    training_dataset = CMU_Dataset(args.data_dir, args.seq_M, args.q_channels, int(sampling_rate * args.window_step),
                                   args.interp_method)
    training_loader = DataLoader(training_dataset, batch_size=args.batch_size, num_workers=4, shuffle=True)

    print('==> Building model..')
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    net = general_FFTNet(radixs=args.radixs, aux_channels=args.num_mfcc + 1, channels=args.fft_channels,
                         classes=args.q_channels).to(device)

    if torch.cuda.device_count() > 1:
        net = torch.nn.DataParallel(net)
    if device == 'cuda':
        cudnn.benchmark = True

    criterion = torch.nn.CrossEntropyLoss()
    optimizer = torch.optim.Adam(net.parameters(), lr=args.lr)

    print("Start Training.")
    a = datetime.now().replace(microsecond=0)
    global_step = 0
    while global_step < args.steps:
        for batch_idx, (inputs, targets, features) in enumerate(training_loader):
            inputs, targets, features = inputs.cuda(), targets.cuda(), features.cuda()

            if args.noise_injecting:
                inputs += torch.randn_like(inputs) / args.q_channels

            optimizer.zero_grad()

            logits = net(inputs, features)[:, :, 1:]
            loss = criterion(logits.unsqueeze(-1), targets.unsqueeze(-1))
            loss.backward()
            optimizer.step()

            print(global_step, "{:.4f}".format(loss.item()))
            global_step += 1
            if global_step > args.steps:
                break

    print("Training time cost:", datetime.now().replace(microsecond=0) - a)

    net = net.module if isinstance(net, torch.nn.DataParallel) else net

    os.makedirs(args.checkpoint_dir, exist_ok=True)
    torch.save(net, args.checkpoint_dir)
    print("Model saved to", args.checkpoint_dir)


if __name__ == '__main__':
    main()