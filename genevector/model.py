import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
import numpy as np
import numpy
import matplotlib.pyplot as plt
from .embedding import GeneEmbedding

class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

class GeneVectorModel(nn.Module):
    """
    GeneVector PyTorch model.

    :param dataset: num_embeddings.
    :type dataset: GeneVector.dataset.GeneVectorDataset
    :param output_file: Flat file to store gene embedding. Input weights and output weights stored in with "2" suffix.
    :type output_file: str
    :param emb_dimension: Number of hidden units and dimension of latent representation.
    :type output_file: int
    :param batch_size: Size to batch gene pairs, defaults to all gene pairs.
    :type output_file: int or None (default).
    :param gain: Scale factor of orthogonal weight initialization.
    :type gain: int
    :param device: Sets Torch device ("cpu", "cuda:0", "mps")
    :type device: str
    """

    def __init__(self, num_embeddings, embedding_dim, gain=1., init_ortho=True):
        self.num_embeddings = num_embeddings
        self.embedding_dim = embedding_dim
        super(GeneVectorModel, self).__init__()
        self.wi = nn.Embedding(num_embeddings, embedding_dim)
        self.wj = nn.Embedding(num_embeddings, embedding_dim)
        if init_ortho:
            nn.init.orthogonal_(self.wi.weight, gain=gain)
            nn.init.orthogonal_(self.wj.weight, gain=gain)
        else:
            self.wi.weight.data.uniform_(-1,1)
            self.wj.weight.data.uniform_(-1,1)

    def forward(self, i_indices, j_indices):
        w_i = self.wi(i_indices)
        w_j = self.wj(j_indices)
        x = torch.sum(w_i * w_j, dim=1)
        return x

    def save_embedding(self, id2word, file_name, layer):
        if layer == 0:
            embedding = self.wi.weight.cpu().data.numpy()
        else:
            embedding = self.wj.weight.cpu().data.numpy()
        with open(file_name, 'w') as f:
            f.write('%d %d\n' % (len(id2word), self.embedding_dim))
            for wid, w in id2word.items():
                e = ' '.join(map(lambda x: str(x), embedding[wid]))
                f.write('%s %s\n' % (w, e))

class GeneVector(object):
    """
    GeneVector framework for training a gene embedding.

    :param dataset: GeneVector dataset.
    :type dataset: GeneVector.dataset.GeneVectorDataset
    :param output_file: Flat file to store gene embedding. Input weights and output weights stored in with "2" suffix.
    :type output_file: str
    :param emb_dimension: Number of hidden units and dimension of latent representation.
    :type output_file: int
    :param batch_size: Size to batch gene pairs, defaults to all gene pairs.
    :type output_file: int or None (default).
    :param gain: Scale factor of orthogonal weight initialization.
    :type gain: int
    :param device: Sets Torch device ("cpu", "cuda:0", "mps")
    :type device: str
    """
    def __init__(self, dataset, output_file, emb_dimension=100, batch_size=None, gain=1, c=100., device="cpu", init_ortho=False):
        """
        Constructor method
        """
        self.dataset = dataset
        self.init_ortho = init_ortho
        self.dataset.create_inputs_outputs(c=c)
        self.output_file_name = output_file
        self.emb_size = len(self.dataset.data.gene2id)
        self.emb_dimension = emb_dimension
        if batch_size == None and self.dataset.num_pairs:
            self.batch_size = self.dataset.num_pairs
        elif batch_size != None:
            self.batch_size = batch_size
        else:
            self.batch_size = 1e6
        self.use_cuda = torch.cuda.is_available()
        self.model = GeneVectorModel(self.emb_size, self.emb_dimension, gain=gain, init_ortho=init_ortho)
        self.device = device
        if self.device == "cuda" and not self.use_cuda:
            raise ValueError("CUDA requested but no GPU available.")
        elif self.device == "cuda":
            self.model.cuda()
        self.optimizer = optim.Adadelta(self.model.parameters())
        self.loss = nn.MSELoss()
        self.epoch = 0
        self.loss_values = list()
        self.mean_loss_values = []


    def train(self, epochs, threshold=None, update_interval=20, alpha=0.0, beta=0.0):
        """
        Trains the model for the specified number of epochs or until the loss falls below the threshold.

        :param epchs: Maximum number of epochs.
        :type epochs: int
        :param threshold: Stopping critera.
        :type threshold: float
        :param update_interval: Number of epochs between printing loss to stdout.
        :type update_interval: int
        :param alpha: Coefficient of orthogonality penalty.
        :type alpha: float
        :param beta: Coefficient of magnitude scaling.
        :type beta: float
        """
        last_loss = 0.
        for _ in range(1, epochs+1):
            batch_i = 0
            for x_ij, i_idx, j_idx in self.dataset.get_batches(self.batch_size):
                batch_i += 1

                outputs = self.model(i_idx, j_idx)
                loss = self.loss(outputs, x_ij) 

                w1 = self.model.wi.weight
                w2 = self.model.wj.weight
                
                #STEP2
                wTw = torch.matmul(w1, w2.t())
                wTw.fill_diagonal_(0)
                t1 = (wTw ** 2).sum()
                t1 = alpha * t1

                #STEP3
                wTw = torch.matmul(w1, w2.t())
                diag = torch.diag(wTw)
                t2 = (diag - self.dataset._ent)
                t2 = (t2 ** 2).sum()
                t2 = beta * t2

                self.optimizer.zero_grad()
                loss = loss + t1 + t2
                loss.backward()
                self.optimizer.step()
                self.loss_values.append(loss.item())
            self.mean_loss_values.append(numpy.mean(self.loss_values[-10:]))
            curr_loss = numpy.mean(self.loss_values[-10:])
            if self.epoch % int(update_interval) == 0:
                print(bcolors.OKGREEN + "**** Epoch" + bcolors.ENDC,
                    self.epoch, 
                    bcolors.HEADER+"\tLoss:"+bcolors.ENDC,
                    round(np.mean(self.loss_values[-30:]),5))
            if type(threshold) == float and abs(curr_loss - last_loss) < threshold:
                print(bcolors.OKCYAN + "Training complete!" + bcolors.ENDC)
                self.model.save_embedding(self.dataset.data.id2gene, self.output_file_name, 0)
                self.model.save_embedding(self.dataset.data.id2gene, self.output_file_name.replace(".vec","2.vec"), 1)

                return
            last_loss = curr_loss
            self.epoch += 1
        print(bcolors.WARNING+"Saving model..."+bcolors.ENDC)
        self.model.save_embedding(self.dataset.data.id2gene, self.output_file_name, 0)
        self.model.save_embedding(self.dataset.data.id2gene, self.output_file_name.replace(".vec","2.vec"), 1)

    def save(self, filepath):
        torch.save(self.model.state_dict(), filepath)

    def load(self, filepath):
        self.gnn.load_state_dict(torch.load(filepath))
        self.gnn.eval()

    def plot(self, fname=None, log=False):
        fig, ax = plt.subplots(1,1,figsize=(12,5),facecolor='#FFFFFF')
        ax.plot(self.mean_loss_values, color="purple")
        ax.set_ylabel('Loss')
        ax.set_xlabel('Epoch')
        if log:
            ax.set_xscale('log')
        if fname != None:
            fig.savefig(fname)